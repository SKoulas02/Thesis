// ---------------------------------------------------------------------------
// host_workload_bcast.cpp -- the workload host for the BROADCAST-VECTOR builds
// (multi_tenant/make_bcast_build.py: 3 x 4x4, 6 x 4x4, 7 x 4x4, 2 x 8x8), MoE mode.
//
// A COPY OF host_workload.cpp (2026-09-28), edited; host_workload.cpp is untouched and
// still runs every other build. What differs, and only this:
//   * the tenants have NO activation movers of their own. ONE pair of broadcast movers,
//     mm2s_bcast_a0 (HBM[0]) and mm2s_bcast_a1 (HBM[1]) -- CUs of krnl_mm2s_bcast<N>,
//     Vitis/krnl_mm2s_bcast.cpp (N = 2, 6, 7) or Vitis/krnl_mm2s_bcast3.cpp -- streams each
//     calculation's vector to all N engines at once. N = the number of tenants given, and
//     that must be EVERY engine of the bitstream, t0 .. t<N-1>: an engine left out would
//     keep that vector in its FIFO and misread the next one. A bitstream built for another
//     N has no krnl_mm2s_bcast<N> and is refused when the kernels are opened.
//   * ONE THREAD launches every mover of every tenant (host_workload.cpp uses one thread per
//     tenant; that races inside XRT's hardware-emulation shim -- see the note at g_nbcast;
//     changed 2026-09-29). The broadcast pair is launched on tenant 0's queue for each
//     calculation. Its events count as INPUT movers of EVERY tenant's calculation (in its
//     span and in its active time), exactly as a tenant's own activation movers do in
//     host_workload -- so the CALC / TENANT / PASS lines mean the same thing as there.
//   * ALWAYS lockstep and ALWAYS one shared vector: every tenant the same number of
//     calculations, and calculation j of every tenant the byte-identical vector (checked).
//     --lockstep and --shared-vector are accepted (run_workload passes them) and implied.
//   * one extra line per calculation of each timed pass, for diagnosis (the parsers ignore
//     it):  BCAST pass calc start_us end_us  (the broadcast pair, relative to the pass).
//
// Everything below this block is host_workload.cpp's description, still true except where
// the list above says otherwise.
//
// host_workload.cpp -- the WORKLOAD host: every tenant runs a LIST of calculations,
// one after another, in its own thread, and all tenants run at the same time.
// (Here: one thread for all tenants -- see above. The tenants still run at the same time
// on the card; only the enqueueing is done by one thread.)
//
// Derived from multi_tenant/host_sparse_multi.cpp, which runs ONE calculation per
// tenant; everything about a single calculation (the per-channel images, the lap
// walk that reads the sparsity code out of the index data, the output format) is
// the same code. What is new:
//
//   * a tenant owns several calculations (one per matrix width in its share of the
//     workload, see workload/plan_workload.py), each with its OWN buffers, all
//     loaded to the card before any timing starts;
//   * before each calculation the tenant's movers are pointed at that
//     calculation's buffers (setArg), then launched, then awaited;
//   * one PASS = every tenant runs its whole list; the pass ends when the last
//     tenant finishes. Timed passes keep every mover's profiling events;
//   * the power soak repeats whole passes, in LOCKSTEP (all tenants start a pass
//     together and the next pass starts when the last one finishes), which is the
//     power profile of running this workload over and over;
//   * tenant "s" drives a SINGLE-ENGINE bitstream (the family builds), whose
//     compute units have no "_t<k>" suffix -- so all 13 architectures are run by
//     this one host.
//
// TWO OPTIONS for the mixture-of-experts test (added 2026-09-28), given BEFORE the xclbin:
//   --lockstep       every tenant waits for all the others after EACH calculation (one
//                    MoE layer: the next token's layer needs every expert's result), not
//                    only at the end of the pass. All tenants must have as many calculations.
//   --shared-vector  calculation j of every tenant reads the FIRST tenant's vector buffers
//                    (the shared-vector bitstreams keep ONE copy of the vector in HBM[0..1]).
//                    Every tenant's vector files must be byte-identical -- checked -- and on
//                    a bitstream WITHOUT shared vector channels the other tenants' movers
//                    cannot reach that buffer, so XRT fails loudly instead of computing.
//
// USAGE
//   host_workload_bcast [--lockstep] [--shared-vector] <xclbin> <clock_MHz> <timed_passes>
//                       <soak_seconds> <k:dir[,dir...]> ...
//     k        tenant index (the k in gemv_tk): 0 .. N-1, every engine exactly once
//     dir,...  that tenant's calculations, run in this order; each holds bin/*.bin
//              and receives output.txt
//     clock_MHz      the kernel clock the card runs the xclbin at (its DATA_CLK)
//     timed_passes   passes with profiling events (at least 1)
//     soak_seconds   0 = no soak; >0 = repeat whole passes for that long, for power
//
// OUTPUT, for run_workload.py (one line each, key=value):
//   CALC  pass tenant calc beats laps start_us active_us end_us  (relative to the pass)
//   TENANT pass tenant calcs busy_us active_us first_start_us last_end_us
//   PASS  pass makespan_us wall_us macs
//   SOAK_START_EPOCH / SOAK_END_EPOCH / SOAK passes seconds
//
// Build (one line; -DCORES/-DBLOCKS = the engine shape of ONE tenant):
//   g++ -Wall -O2 -std=c++1y -I$XILINX_XRT/include host_workload_bcast.cpp -L$XILINX_XRT/lib -lOpenCL -lrt -lstdc++ -pthread -DCORES=8 -DBLOCKS=8 -o host_workload_bcast
// ---------------------------------------------------------------------------

#define CL_HPP_CL_1_2_DEFAULT_BUILD
#define CL_HPP_TARGET_OPENCL_VERSION 120
#define CL_HPP_MINIMUM_OPENCL_VERSION 120
#define CL_HPP_ENABLE_PROGRAM_CONSTRUCTION_FROM_ARRAY_COMPATIBILITY 1

#include <CL/cl2.hpp>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <memory>
#include <string>
#include <utility>
#include <vector>

// ---- the engine shape of one tenant: every width follows from CORES x BLOCKS ----
#ifndef CORES
#define CORES 4
#endif
#ifndef BLOCKS
#define BLOCKS 4
#endif
static const int PC_BYTES = 32;
static const int LANES = CORES * BLOCKS;
static const int W_PCS = (32 * LANES + 255) / 256;
static const int IND_PCS = (10 * LANES + 2 + 255) / 256;
static const int A_PCS = 2;
static const int C_PCS = (16 * LANES + 255) / 256;
static const int WIN_ELEMS = 32;
static const size_t ALIGN = 4096;
static const int IND_BITS = 10 * LANES;               // the sparsity code sits above them
static const int SP_PC = IND_BITS / 256;
static const int SP_BYTE = (IND_BITS % 256) / 8;

static const char* SP_NAME[4] = { "2:4", "2:8", "2:16", "2:32" };
static inline int sp_freeze(unsigned code) { return 32 / (4 << code); }

static bool g_lockstep = true;          // always (--lockstep accepted, implied)
static bool g_shared_vector = true;     // always (--shared-vector accepted, implied)

// The broadcast pair: N (= the tenants = the outputs of krnl_mm2s_bcast<N>), and per
// calculation j the pair's events of the current timed pass.
static int g_nbcast = 0;
static std::vector<std::vector<cl::Event> > g_ev_bcast;

// ONE THREAD LAUNCHES EVERYTHING (changed 2026-09-29; the first version ran one thread per
// tenant, like host_workload.cpp). In HARDWARE EMULATION, XRT 2022.1 (the server's 2.13)
// looks every launched command buffer up in a std::map WITHOUT a lock
// (core/pcie/emulation/hw_em/generic_pcie_hal2/shim.cxx:2503, called unlocked by
// xclExecBuf at :3053) while xclAllocBO inserts into the same map under a mutex (:2594).
// Several tenant threads launching at once race on that map, and a launch fails with
// "[XRT] ERROR: failed to launch execution buffer: Operation not permitted" -- the shim's
// generic -1, printed as errno 1 because xrt_core::system_error takes std::abs
// (core/common/error.h:34). All three broadcast hw_emu gates failed exactly so, in their
// first calculation, on 2026-09-29. The host that PASSED hw_emu (host_sparse_multi) launches
// from one thread. On the card the driver path is thread-safe; one thread is harmless
// there, and it is how the planned native-XRT host works too.

#define OCL_CHECK(err, expr)                                                   \
    do {                                                                       \
        (expr);                                                                \
        if ((err) != CL_SUCCESS) {                                             \
            std::fprintf(stderr, "OpenCL error %d at %s:%d\n", (int)(err),     \
                         __FILE__, __LINE__);                                  \
            std::exit(1);                                                      \
        }                                                                      \
    } while (0)

// ---- 4096-byte aligned buffer (as in the other hosts) ------------------------
struct AlignedBuf {
    unsigned char* p;
    size_t n;
    AlignedBuf() : p(NULL), n(0) {}
    void alloc(size_t bytes) {
        n = bytes;
        void* q = NULL;
        if (posix_memalign(&q, ALIGN, bytes ? bytes : ALIGN)) {
            std::fprintf(stderr, "posix_memalign failed for %zu bytes\n", bytes);
            std::exit(1);
        }
        p = (unsigned char*)q;
        std::memset(p, 0, n);
    }
    ~AlignedBuf() { if (p) free(p); }
private:
    AlignedBuf(const AlignedBuf&);
    AlignedBuf& operator=(const AlignedBuf&);
};

static void read_into(const std::string& path, AlignedBuf& buf) {
    std::ifstream f(path.c_str(), std::ios::binary | std::ios::ate);
    if (!f) {
        std::fprintf(stderr, "cannot open %s\n", path.c_str());
        std::exit(1);
    }
    std::streamsize n = f.tellg();
    f.seekg(0, std::ios::beg);
    buf.alloc((size_t)n);
    f.read((char*)buf.p, n);
}

static std::vector<unsigned char> read_file(const std::string& path) {
    std::ifstream f(path.c_str(), std::ios::binary | std::ios::ate);
    if (!f) {
        std::fprintf(stderr, "cannot open %s\n", path.c_str());
        std::exit(1);
    }
    std::streamsize n = f.tellg();
    f.seekg(0, std::ios::beg);
    std::vector<unsigned char> b((size_t)n);
    f.read((char*)b.data(), n);
    return b;
}

static inline unsigned sparsity_at(const AlignedBuf& ind_sp, size_t k) {
    return (unsigned)(ind_sp.p[k * PC_BYTES + SP_BYTE] & 0x3);
}

static double now_epoch() {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static cl::Device pick_u280() {
    std::vector<cl::Platform> platforms;
    cl::Platform::get(&platforms);
    for (size_t p = 0; p < platforms.size(); ++p) {
        std::string pname = platforms[p].getInfo<CL_PLATFORM_NAME>();
        if (pname.find("Xilinx") == std::string::npos) continue;
        std::vector<cl::Device> devs;
        platforms[p].getDevices(CL_DEVICE_TYPE_ACCELERATOR, &devs);
        for (size_t d = 0; d < devs.size(); ++d) {
            std::string dname = devs[d].getInfo<CL_DEVICE_NAME>();
            if (dname.find("u280") != std::string::npos ||
                dname.find("U280") != std::string::npos) {
                std::printf("  device: %s\n", dname.c_str());
                return devs[d];
            }
        }
    }
    std::fprintf(stderr, "no U280 found -- refusing to run on the wrong card\n");
    std::exit(1);
}

// ---------------------------------------------------------------------------
// One calculation: one stimulus directory = one activation vector, any number of
// laps, the sparsity free to change from lap to lap.
// ---------------------------------------------------------------------------
struct Calc {
    std::string dir;
    std::vector<AlignedBuf> w_img, i_img, a_img, c_img;
    size_t n_weight_beats, n_act_beats, n_out_beats;
    std::vector<cl::Buffer> b_w, b_i, b_a, b_c;
    std::vector<cl::Memory> to_dev, from_dev;
    std::vector<cl::Event> ev;          // this pass: outputs first, then every input mover
    cl_ulong t_start, t_active, t_end;

    Calc() : w_img(W_PCS), i_img(IND_PCS), a_img(A_PCS), c_img(C_PCS),
             n_weight_beats(0), n_act_beats(0), n_out_beats(0),
             b_w(W_PCS), b_i(IND_PCS), b_a(A_PCS), b_c(C_PCS),
             t_start(0), t_active(0), t_end(0) {}
    size_t laps() const { return n_out_beats; }
    double macs() const { return (double)n_weight_beats * LANES * 2.0; }
};

struct Tenant {
    int id;                             // 0 .. N-1 (a broadcast build has no single engine)
    std::vector<std::unique_ptr<Calc> > calcs;
    std::vector<cl::Kernel> k_w, k_i, k_c;
    bool drives_bcast;                  // tenant 0: owns the broadcast pair and the vectors
    std::vector<cl::Kernel> k_b;        // mm2s_bcast_a0, mm2s_bcast_a1 (tenant 0 only)
    cl::CommandQueue q;

    Tenant() : id(0), k_w(W_PCS), k_i(IND_PCS), k_c(C_PCS), drives_bcast(false),
               k_b(A_PCS) {}
    std::string suffix() const { return id < 0 ? std::string() : "_t" + std::to_string(id); }
    std::string tag() const { return id < 0 ? std::string("s") : "t" + std::to_string(id); }
};

// Read one calculation's images and walk its laps (the multi-tenant host's logic).
static void load_calc(Calc& C, const std::string& who) {
    const std::string bindir = C.dir + "/bin";
    for (int i = 0; i < W_PCS; ++i)
        read_into(bindir + "/weights_pc" + std::to_string(i) + ".bin", C.w_img[i]);
    for (int i = 0; i < IND_PCS; ++i)
        read_into(bindir + "/ind_pc" + std::to_string(i) + ".bin", C.i_img[i]);
    for (int i = 0; i < A_PCS; ++i)
        read_into(bindir + "/act_pc" + std::to_string(i) + ".bin", C.a_img[i]);

    C.n_weight_beats = C.w_img[0].n / PC_BYTES;
    const size_t n_index_beats = C.i_img[0].n / PC_BYTES;
    C.n_act_beats = C.a_img[0].n / PC_BYTES;
    if (n_index_beats != C.n_weight_beats) {
        std::fprintf(stderr, "%s %s: %zu index beats vs %zu weight beats -- they are joined "
                     "beat-for-beat; a mismatch hangs the engine.\n", who.c_str(),
                     C.dir.c_str(), n_index_beats, C.n_weight_beats);
        std::exit(1);
    }
    if (C.n_act_beats == 0 || C.n_weight_beats == 0) {
        std::fprintf(stderr, "%s %s: empty stimulus\n", who.c_str(), C.dir.c_str());
        std::exit(1);
    }

    size_t pos = 0, laps = 0, per_code[4] = { 0, 0, 0, 0 };
    while (pos < C.n_weight_beats) {
        const unsigned code = sparsity_at(C.i_img[SP_PC], pos);
        const size_t nb = C.n_act_beats * (size_t)sp_freeze(code);
        if (pos + nb > C.n_weight_beats) {
            std::fprintf(stderr, "%s %s: stimulus truncated -- lap %zu at %s needs %zu beats, "
                         "%zu remain\n", who.c_str(), C.dir.c_str(), laps, SP_NAME[code], nb,
                         C.n_weight_beats - pos);
            std::exit(1);
        }
        for (size_t k = pos; k < pos + nb; ++k) {
            if (sparsity_at(C.i_img[SP_PC], k) != code) {
                std::fprintf(stderr, "%s %s: sparsity code changes mid-lap at beat %zu\n",
                             who.c_str(), C.dir.c_str(), k);
                std::exit(1);
            }
        }
        ++laps;
        ++per_code[code];
        pos += nb;
    }
    C.n_out_beats = laps;
    for (int i = 0; i < C_PCS; ++i) C.c_img[i].alloc(C.n_out_beats * PC_BYTES);
    std::printf("%s calc %s: %zu weight beats, %zu laps (2:4 %zu, 2:8 %zu, 2:16 %zu, "
                "2:32 %zu), V=%zu\n", who.c_str(), C.dir.c_str(), C.n_weight_beats, laps,
                per_code[0], per_code[1], per_code[2], per_code[3],
                C.n_act_beats * WIN_ELEMS);
}

// Point every mover of the tenant at calculation C's buffers (tenant 0: also the
// broadcast pair at the vector, which only tenant 0's calculations hold).
static void set_args(Tenant& T, Calc& C) {
    for (int i = 0; i < W_PCS; ++i) {
        T.k_w[i].setArg(0, C.b_w[i]);
        T.k_w[i].setArg(2, (unsigned)C.n_weight_beats);   // arg 1 is the stream
    }
    for (int i = 0; i < IND_PCS; ++i) {
        T.k_i[i].setArg(0, C.b_i[i]);
        T.k_i[i].setArg(2, (unsigned)C.n_weight_beats);   // joined with the weights
    }
    if (T.drives_bcast) {
        for (int i = 0; i < A_PCS; ++i) {
            T.k_b[i].setArg(0, C.b_a[i]);
            // args 1..N are the N streams, so n_beats is arg N + 1
            T.k_b[i].setArg(g_nbcast + 1, (unsigned)C.n_act_beats);
        }
    }
    for (int i = 0; i < C_PCS; ++i) {
        T.k_c[i].setArg(1, C.b_c[i]);                     // arg 0 is the stream
        T.k_c[i].setArg(2, (unsigned)C.n_out_beats);
    }
}

// Kernels, queue, and every calculation's buffers, loaded to the card. A buffer takes
// the HBM bank of the argument it is first bound to, so each calculation's buffers are
// bound (set_args) BEFORE they are migrated.
// Tenant 0 (drives_bcast) also opens the broadcast pair and holds the ONE copy of every
// calculation's vector, bound to the pair -- so it lands in HBM[0] / HBM[1]. The other
// tenants have no vector buffers at all: their engines get the vector from the pair.
static void bind_tenant(Tenant& T, cl::Program& program, cl::Context& context,
                        cl::Device& device) {
    cl_int err = CL_SUCCESS;
    const std::string s = T.suffix();
    // OUT-OF-ORDER is load-bearing: on an in-order queue the movers of one calculation
    // serialise and a producer waits on a consumer that cannot start.
    OCL_CHECK(err, T.q = cl::CommandQueue(context, device,
                                          CL_QUEUE_PROFILING_ENABLE |
                                          CL_QUEUE_OUT_OF_ORDER_EXEC_MODE_ENABLE, &err));
    for (int i = 0; i < W_PCS; ++i)
        OCL_CHECK(err, T.k_w[i] = cl::Kernel(program,
                  ("krnl_mm2s:{mm2s_w" + std::to_string(i) + s + "}").c_str(), &err));
    for (int i = 0; i < IND_PCS; ++i)
        OCL_CHECK(err, T.k_i[i] = cl::Kernel(program,
                  ("krnl_mm2s:{mm2s_i" + std::to_string(i) + s + "}").c_str(), &err));
    if (T.drives_bcast) {
        for (int i = 0; i < A_PCS; ++i) {
            const std::string name = "krnl_mm2s_bcast" + std::to_string(g_nbcast) +
                                     ":{mm2s_bcast_a" + std::to_string(i) + "}";
            T.k_b[i] = cl::Kernel(program, name.c_str(), &err);
            if (err != CL_SUCCESS) {
                std::fprintf(stderr, "this bitstream has no %s (OpenCL error %d) -- it is not a "
                             "broadcast build for %d engines. Give EVERY engine of the "
                             "bitstream, t0 .. t<N-1>, and use host_workload for any other "
                             "build.\n", name.c_str(), (int)err, g_nbcast);
                std::exit(1);
            }
        }
    }
    for (int i = 0; i < C_PCS; ++i)
        OCL_CHECK(err, T.k_c[i] = cl::Kernel(program,
                  ("krnl_s2mm:{s2mm_c" + std::to_string(i) + s + "}").c_str(), &err));

    for (size_t j = 0; j < T.calcs.size(); ++j) {
        Calc& C = *T.calcs[j];
        for (int i = 0; i < W_PCS; ++i) {
            OCL_CHECK(err, C.b_w[i] = cl::Buffer(context, CL_MEM_USE_HOST_PTR | CL_MEM_READ_ONLY,
                                                 C.w_img[i].n, C.w_img[i].p, &err));
            C.to_dev.push_back(C.b_w[i]);
        }
        for (int i = 0; i < IND_PCS; ++i) {
            OCL_CHECK(err, C.b_i[i] = cl::Buffer(context, CL_MEM_USE_HOST_PTR | CL_MEM_READ_ONLY,
                                                 C.i_img[i].n, C.i_img[i].p, &err));
            C.to_dev.push_back(C.b_i[i]);
        }
        for (int i = 0; i < A_PCS && T.drives_bcast; ++i) {   // the ONE copy: tenant 0
            OCL_CHECK(err, C.b_a[i] = cl::Buffer(context, CL_MEM_USE_HOST_PTR | CL_MEM_READ_ONLY,
                                                 C.a_img[i].n, C.a_img[i].p, &err));
            C.to_dev.push_back(C.b_a[i]);
        }
        for (int i = 0; i < C_PCS; ++i) {
            OCL_CHECK(err, C.b_c[i] = cl::Buffer(context, CL_MEM_USE_HOST_PTR | CL_MEM_WRITE_ONLY,
                                                 C.c_img[i].n, C.c_img[i].p, &err));
            C.from_dev.push_back(C.b_c[i]);
        }
        set_args(T, C);
        OCL_CHECK(err, err = T.q.enqueueMigrateMemObjects(C.to_dev, 0));
        T.q.finish();
    }
}

// Enqueue one mover and, for a timed pass, keep its event.
static void launch(cl::CommandQueue& q, cl::Kernel& k, std::vector<cl::Event>* keep) {
    cl_int err = CL_SUCCESS;
    cl::Event e;
    OCL_CHECK(err, err = q.enqueueTask(k, NULL, &e));
    if (keep) keep->push_back(e);
}

// One pass, from ONE thread (see the note at g_nbcast): for every calculation j, point every
// tenant's movers at it; enqueue the OUTPUT movers of every tenant first (every sink ready
// before any source pushes), then the broadcast pair (calculation j's vector, to every
// engine, on tenant 0's queue), then every tenant's index movers, then its weight movers;
// then finish every queue. Lockstep by construction: nothing of calculation j + 1 is
// enqueued before every tenant has finished j. A tenant's events stay in the order
// calc_times() expects: its outputs first, then its inputs.
static void run_pass(std::vector<std::unique_ptr<Tenant> >& ts, bool keep_events) {
    const size_t ncalc = ts[0]->calcs.size();
    for (size_t j = 0; j < ncalc; ++j) {
        for (size_t t = 0; t < ts.size(); ++t) {
            set_args(*ts[t], *ts[t]->calcs[j]);
            if (keep_events) ts[t]->calcs[j]->ev.clear();
        }
        if (keep_events) g_ev_bcast[j].clear();
        for (size_t t = 0; t < ts.size(); ++t)
            for (int i = 0; i < C_PCS; ++i)
                launch(ts[t]->q, ts[t]->k_c[i], keep_events ? &ts[t]->calcs[j]->ev : NULL);
        for (size_t t = 0; t < ts.size(); ++t)
            if (ts[t]->drives_bcast)
                for (int i = 0; i < A_PCS; ++i)
                    launch(ts[t]->q, ts[t]->k_b[i], keep_events ? &g_ev_bcast[j] : NULL);
        for (size_t t = 0; t < ts.size(); ++t)
            for (int i = 0; i < IND_PCS; ++i)
                launch(ts[t]->q, ts[t]->k_i[i], keep_events ? &ts[t]->calcs[j]->ev : NULL);
        for (size_t t = 0; t < ts.size(); ++t)
            for (int i = 0; i < W_PCS; ++i)
                launch(ts[t]->q, ts[t]->k_w[i], keep_events ? &ts[t]->calcs[j]->ev : NULL);
        for (size_t t = 0; t < ts.size(); ++t) ts[t]->q.finish();
    }
}

// span = first start -> last end of the calculation's movers; active = from the latest
// START among its input movers (the join cannot run before every input flows) to the end.
// The broadcast pair (bev) is an input mover of every tenant's calculation.
static void calc_times(Calc& C, const std::vector<cl::Event>& bev) {
    C.t_start = ~(cl_ulong)0;
    C.t_end = 0;
    C.t_active = 0;
    for (size_t i = 0; i < C.ev.size(); ++i) {
        const cl_ulong s = C.ev[i].getProfilingInfo<CL_PROFILING_COMMAND_START>();
        const cl_ulong e = C.ev[i].getProfilingInfo<CL_PROFILING_COMMAND_END>();
        C.t_start = std::min(C.t_start, s);
        C.t_end = std::max(C.t_end, e);
        if (i >= (size_t)C_PCS) C.t_active = std::max(C.t_active, s);
    }
    for (size_t i = 0; i < bev.size(); ++i) {
        const cl_ulong s = bev[i].getProfilingInfo<CL_PROFILING_COMMAND_START>();
        const cl_ulong e = bev[i].getProfilingInfo<CL_PROFILING_COMMAND_END>();
        C.t_start = std::min(C.t_start, s);
        C.t_end = std::max(C.t_end, e);
        C.t_active = std::max(C.t_active, s);
    }
}

static void write_output(const Calc& C) {
    const std::string outpath = C.dir + "/output.txt";
    std::FILE* of = std::fopen(outpath.c_str(), "w");
    if (!of) {
        std::fprintf(stderr, "cannot write %s\n", outpath.c_str());
        std::exit(1);
    }
    for (size_t k = 0; k < C.n_out_beats; ++k) {
        for (int r = 0; r < LANES; ++r) {
            const unsigned char* p = &C.c_img[r / 16].p[k * PC_BYTES + (r % 16) * 2];
            std::fprintf(of, "%04X\n", (unsigned)p[0] | ((unsigned)p[1] << 8));
        }
    }
    std::fclose(of);
}

static std::vector<std::string> split_commas(const std::string& s) {
    std::vector<std::string> out;
    size_t a = 0;
    while (a <= s.size()) {
        const size_t b = s.find(',', a);
        const std::string part = s.substr(a, b == std::string::npos ? std::string::npos : b - a);
        if (!part.empty()) out.push_back(part);
        if (b == std::string::npos) break;
        a = b + 1;
    }
    return out;
}

int main(int argc, char** argv) {
    int ai = 1;                                  // leading options, then the positionals
    while (ai < argc && std::strncmp(argv[ai], "--", 2) == 0) {
        const std::string opt = argv[ai++];
        if (opt == "--lockstep") {
            g_lockstep = true;                   // implied anyway
        } else if (opt == "--shared-vector") {
            g_shared_vector = true;              // implied anyway
        } else {
            std::fprintf(stderr, "unknown option %s (--lockstep, --shared-vector)\n", opt.c_str());
            return 1;
        }
    }
    if (argc - ai < 5) {
        std::fprintf(stderr,
            "usage: %s [--lockstep] [--shared-vector] <xclbin> <clock_MHz> <timed_passes> "
            "<soak_seconds> <k:dir[,dir...]> ...\n"
            "  k   tenant index (the k in gemv_tk): every engine of the broadcast build, 0 .. N-1\n"
            "  (lockstep and one shared vector are always on; the two options are accepted)\n",
            argv[0]);
        return 1;
    }
    const std::string xclbin_path = argv[ai];
    const double clock_mhz = atof(argv[ai + 1]);
    const int timed_passes = atoi(argv[ai + 2]);
    const double soak_seconds = atof(argv[ai + 3]);
    if (clock_mhz <= 0.0 || timed_passes < 1) {
        std::fprintf(stderr, "clock_MHz must be positive and timed_passes at least 1\n");
        return 1;
    }

    std::vector<std::unique_ptr<Tenant> > ts;
    for (int a = ai + 4; a < argc; ++a) {
        const std::string arg = argv[a];
        const size_t colon = arg.find(':');
        if (colon == std::string::npos || colon == 0 || colon + 1 >= arg.size()) {
            std::fprintf(stderr, "bad tenant spec '%s' -- expected k:dir[,dir...]\n", arg.c_str());
            return 1;
        }
        std::unique_ptr<Tenant> T(new Tenant());
        const std::string k = arg.substr(0, colon);
        if (k.find_first_not_of("0123456789") != std::string::npos) {
            std::fprintf(stderr, "tenant '%s': a broadcast build has only engines 0 .. N-1 (no "
                         "single-engine tenant s)\n", k.c_str());
            return 1;
        }
        T->id = atoi(k.c_str());
        for (size_t j = 0; j < ts.size(); ++j) {
            if (ts[j]->id == T->id) {
                std::fprintf(stderr, "tenant %s given twice\n", k.c_str());
                return 1;
            }
        }
        const std::vector<std::string> dirs = split_commas(arg.substr(colon + 1));
        for (size_t j = 0; j < dirs.size(); ++j) {
            std::unique_ptr<Calc> C(new Calc());
            C->dir = dirs[j];
            T->calcs.push_back(std::move(C));
        }
        ts.push_back(std::move(T));
    }
    // Every engine of the bitstream, each once: the pair feeds all N of them, so an engine
    // left out would keep a vector in its FIFO. Sorted, so tenant 0 is ts[0].
    std::sort(ts.begin(), ts.end(), [](const std::unique_ptr<Tenant>& x,
                                       const std::unique_ptr<Tenant>& y) { return x->id < y->id; });
    for (size_t i = 0; i < ts.size(); ++i) {
        if (ts[i]->id != (int)i) {
            std::fprintf(stderr, "tenants must be exactly 0 .. N-1 (every engine of the broadcast "
                         "build, once each); tenant %zu is missing\n", i);
            return 1;
        }
    }
    if (ts.size() < 2) {
        std::fprintf(stderr, "a broadcast build has at least 2 engines -- give all of them\n");
        return 1;
    }
    g_nbcast = (int)ts.size();
    ts[0]->drives_bcast = true;

    // Old outputs go first, before anything can fail, so a compare can never read a
    // previous run's result.
    for (size_t i = 0; i < ts.size(); ++i)
        for (size_t j = 0; j < ts[i]->calcs.size(); ++j)
            std::remove((ts[i]->calcs[j]->dir + "/output.txt").c_str());
    if (!std::getenv("XILINX_XRT")) {
        std::fprintf(stderr, "XILINX_XRT is not set -- run: source /opt/xilinx/xrt/setup.sh\n");
        return 1;
    }

    std::printf("workload host (broadcast): %zu tenant(s) of %dx%d, %d timed pass(es), soak %.0f s"
                ", lockstep after every calculation, ONE vector copy sent to every engine by "
                "krnl_mm2s_bcast%d\n", ts.size(), CORES, BLOCKS, timed_passes, soak_seconds,
                g_nbcast);
    if (g_lockstep || g_shared_vector) {
        for (size_t i = 1; i < ts.size(); ++i) {
            if (ts[i]->calcs.size() != ts[0]->calcs.size()) {
                std::fprintf(stderr, "--lockstep/--shared-vector: tenant %s has %zu calculations, "
                             "tenant %s has %zu -- they must be equal\n", ts[i]->tag().c_str(),
                             ts[i]->calcs.size(), ts[0]->tag().c_str(), ts[0]->calcs.size());
                return 1;
            }
        }
    }
    double pass_macs = 0.0;
    for (size_t i = 0; i < ts.size(); ++i)
        for (size_t j = 0; j < ts[i]->calcs.size(); ++j) {
            load_calc(*ts[i]->calcs[j], ts[i]->tag());
            pass_macs += ts[i]->calcs[j]->macs();
        }
    if (g_shared_vector) {
        // one buffer for everyone: every tenant's vector must be the same, byte for byte
        for (size_t i = 1; i < ts.size(); ++i)
            for (size_t j = 0; j < ts[i]->calcs.size(); ++j)
                for (int p = 0; p < A_PCS; ++p) {
                    const AlignedBuf& x = ts[0]->calcs[j]->a_img[p];
                    const AlignedBuf& y = ts[i]->calcs[j]->a_img[p];
                    if (x.n != y.n || std::memcmp(x.p, y.p, x.n) != 0) {
                        std::fprintf(stderr, "broadcast: %s act_pc%d.bin differs from %s -- "
                                     "the tenants do not share one vector\n",
                                     ts[i]->calcs[j]->dir.c_str(), p, ts[0]->calcs[j]->dir.c_str());
                        return 1;
                    }
                }
    }
    g_ev_bcast.assign(ts[0]->calcs.size(), std::vector<cl::Event>());

    cl_int err = CL_SUCCESS;
    cl::Device device = pick_u280();
    cl::Context context(device, NULL, NULL, NULL, &err);
    std::vector<unsigned char> bits = read_file(xclbin_path);
    cl::Program::Binaries bins;
    bins.push_back(std::make_pair((const void*)bits.data(), bits.size()));
    std::vector<cl::Device> devs(1, device);
    cl::Program program(context, devs, bins, NULL, &err);
    std::printf("xclbin loaded: %s\n", xclbin_path.c_str());
    for (size_t i = 0; i < ts.size(); ++i)
        bind_tenant(*ts[i], program, context, device);
    std::printf("all data on the card; %.0f MACs per pass (padding included)\n", pass_macs);
    std::fflush(stdout);

    // ---- timed passes ----------------------------------------------------------
    for (int p = 0; p < timed_passes; ++p) {
        const double w0 = now_epoch();
        run_pass(ts, true);
        const double wall_us = (now_epoch() - w0) * 1e6;
        cl_ulong origin = ~(cl_ulong)0, last = 0;
        for (size_t i = 0; i < ts.size(); ++i)
            for (size_t j = 0; j < ts[i]->calcs.size(); ++j) {
                Calc& C = *ts[i]->calcs[j];
                calc_times(C, g_ev_bcast[j]);
                origin = std::min(origin, C.t_start);
                last = std::max(last, C.t_end);
            }
        for (size_t j = 0; j < g_ev_bcast.size(); ++j) {
            cl_ulong bs = ~(cl_ulong)0, be = 0;
            for (size_t i = 0; i < g_ev_bcast[j].size(); ++i) {
                bs = std::min(bs, g_ev_bcast[j][i].getProfilingInfo<CL_PROFILING_COMMAND_START>());
                be = std::max(be, g_ev_bcast[j][i].getProfilingInfo<CL_PROFILING_COMMAND_END>());
            }
            std::printf("BCAST pass=%d calc=%zu start_us=%.3f end_us=%.3f\n", p, j,
                        (bs - origin) / 1e3, (be - origin) / 1e3);
        }
        for (size_t i = 0; i < ts.size(); ++i) {
            Tenant& T = *ts[i];
            double busy = 0.0, active = 0.0;
            cl_ulong first = ~(cl_ulong)0, end = 0;
            for (size_t j = 0; j < T.calcs.size(); ++j) {
                const Calc& C = *T.calcs[j];
                std::printf("CALC pass=%d tenant=%s calc=%zu beats=%zu laps=%zu start_us=%.3f "
                            "active_us=%.3f end_us=%.3f\n", p, T.tag().c_str(), j,
                            C.n_weight_beats, C.laps(), (C.t_start - origin) / 1e3,
                            (C.t_active - origin) / 1e3, (C.t_end - origin) / 1e3);
                busy += (C.t_end - C.t_start) / 1e3;
                active += (C.t_end - C.t_active) / 1e3;
                first = std::min(first, C.t_start);
                end = std::max(end, C.t_end);
            }
            std::printf("TENANT pass=%d tenant=%s calcs=%zu busy_us=%.3f active_us=%.3f "
                        "first_start_us=%.3f last_end_us=%.3f\n", p, T.tag().c_str(),
                        T.calcs.size(), busy, active, (first - origin) / 1e3,
                        (end - origin) / 1e3);
        }
        std::printf("PASS pass=%d makespan_us=%.3f wall_us=%.3f macs=%.0f\n", p,
                    (last - origin) / 1e3, wall_us, pass_macs);
        std::fflush(stdout);
    }

    // ---- outputs of the last timed pass (the correctness check reads these) ------
    for (size_t i = 0; i < ts.size(); ++i) {
        for (size_t j = 0; j < ts[i]->calcs.size(); ++j)
            OCL_CHECK(err, err = ts[i]->q.enqueueMigrateMemObjects(ts[i]->calcs[j]->from_dev,
                                                                   CL_MIGRATE_MEM_OBJECT_HOST));
        ts[i]->q.finish();
        for (size_t j = 0; j < ts[i]->calcs.size(); ++j) write_output(*ts[i]->calcs[j]);
    }
    std::printf("outputs written (last timed pass)\n");

    // ---- power soak: whole passes in lockstep ------------------------------------
    if (soak_seconds > 0.0) {
        const double t0 = now_epoch();
        std::printf("SOAK_START_EPOCH %.3f\n", t0);
        std::fflush(stdout);
        size_t passes = 0;
        while (now_epoch() - t0 < soak_seconds) {
            run_pass(ts, false);
            ++passes;
        }
        const double t1 = now_epoch();
        std::printf("SOAK_END_EPOCH %.3f\n", t1);
        std::printf("SOAK passes=%zu seconds=%.3f\n", passes, t1 - t0);
        std::fflush(stdout);
    }
    // Release the broadcast events NOW, while the context and device (locals of main) still
    // exist. As a global, g_ev_bcast would otherwise be destroyed by the exit handlers AFTER
    // main's locals, and releasing an event then touches a freed XRT device: the hw_emu gate of
    // x3_bcast crashed exactly there, after a complete run (gdb, 2026-09-29: ~vector
    // <g_ev_bcast> -> clReleaseEvent -> xrt_core::context_mgr::close_context, SIGSEGV).
    g_ev_bcast.clear();
    return 0;
}
