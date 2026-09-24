// ---------------------------------------------------------------------------
// host_sparse_multi.cpp -- N INDEPENDENT 4x4 GEMV TENANTS ON ONE U280.
//
// Derived from Vitis/host_sparse.cpp, which drives ONE engine. Everything about
// a single tenant -- the per-PC images, the lap walk that reads the sparsity
// code out of the index data, the beat accounting, the output format -- is the
// same code. What is new is that all of it now lives in a Tenant struct and
// there are N of them, each with:
//
//   * its OWN matrix       (different shape, sparsity, seed -- see prep_tenants.py)
//   * its OWN 6 HBM pseudo-channels and its own 7 compute units
//   * its OWN command queue, so tenants are not serialised behind each other
//   * its OWN span, throughput and golden file
//
// WHY INDEPENDENT MATRICES AND NOT COPIES OF ONE. The claim under test is
// multi-tenancy: several unrelated jobs sharing the card. Identical stimulus
// would also make a mis-wired stream_connect INVISIBLE -- every tenant would
// read plausible data from the wrong neighbour and still match golden. With
// different matrices per tenant, any cross-wiring fails the compare at once.
//
// ---- THE CU NAMING CONTRACT ----------------------------------------------
// This file and the generated link config must agree exactly. Tenant k owns:
//
//     mm2s_w0_tk  mm2s_w1_tk      weights   -> HBM[6k+0], HBM[6k+1]
//     mm2s_i0_tk                  indices   -> HBM[6k+2]
//     mm2s_a0_tk  mm2s_a1_tk      activations -> HBM[6k+3], HBM[6k+4]
//     gemv_tk                     the engine (free-running, no setArg, no enqueue)
//     s2mm_c0_tk                  results   -> HBM[6k+5]
//
// Both sides are generated from the same table in make_multi_build.py. If you
// rename a CU there, this file stops finding it -- a clean runtime error, not a
// wrong answer.
//
// ---- WHAT IS MEASURED ------------------------------------------------------
// PER TENANT: the span of ITS OWN compute units (earliest start -> latest end
// among its 6 movers). That is tenant k's latency whether or not anyone else is
// running, which is what makes the interference experiment possible:
//
//     run tenant 0 alone          ->  its span, uncontended
//     run tenants 0..N-1 together ->  its span, contended
//     interference = contended / uncontended
//
// AGGREGATE: the union window (first start -> last end across all tenants) and
// the OVERLAP window (last start -> first end), i.e. the period during which
// every tenant really was running. A slowdown measured over a window where the
// tenants barely overlapped would be meaningless, so the overlap fraction is
// printed with every aggregate number.
//
// The engines are free-running: they are never enqueued and never appear in any
// span. Only the movers are launched, exactly as in the single-tenant host.
//
// ---- USAGE -----------------------------------------------------------------
//     host_sparse_multi <xclbin> <clock_MHz> <soak_seconds> <k:dir> [<k:dir> ...]
//
//   k:dir   tenant INDEX (the k in gemv_tk) and its stimulus directory. The
//           directory holds bin/{weights_pc*,ind_pc*,act_pc*}.bin and golden.txt,
//           and receives output.txt. Pass one pair to run a tenant ALONE on a
//           multi-tenant bitstream; pass all of them to run them together.
//   clock_MHz      the kernel clock the card RUNS at: xclbin DATA_CLK, which is
//                  not always the link target (x4 linked at 375 runs at 374).
//   soak_seconds   0 = off. >0 runs every tenant back-to-back in its own THREAD
//                  for that long, buffers resident, for power measurement. The
//                  load window is printed as epoch seconds for the sampler.
//   --lockstep     (anywhere after soak_seconds) the soak instead runs the tenants
//                  as ONE job: launch every tenant, wait for ALL of them, repeat.
//                  That is the power profile of a SHARED workload, where the fast
//                  tenants idle until the slowest quarter is done. Without it the
//                  fast tenants would loop on their own and overstate the power.
//
// Example -- two tenants together, then tenant 1 alone:
//     ./host_sparse_multi sparse_4x4_x2_400.xclbin 400 0 0:t0 1:t1
//     ./host_sparse_multi sparse_4x4_x2_400.xclbin 400 0 1:t1
//
// Build (same flags as the single-tenant host; -pthread is load-bearing here).
// KEEP IT ON ONE LINE: a backslash ending a // comment line makes GCC warn
// "multi-line comment" on every compile.
//   g++ -Wall -O2 -std=c++1y -I$XILINX_XRT/include host_sparse_multi.cpp -L$XILINX_XRT/lib -lOpenCL -lrt -lstdc++ -pthread -o host_sparse_multi
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
#include <iostream>
#include <memory>
#include <string>
#include <thread>
#include <utility>
#include <vector>

// ---- the tenant's engine shape ---------------------------------------------
// One tenant = ONE engine of CORES x BLOCKS. The default is 4x4; build the binary
// for another shape with -DCORES=8 -DBLOCKS=4, which is what the 3 x 8x4 build
// uses. Every width below follows from T = CORES x BLOCKS exactly as the family
// generator derives it (2 weights of 16 bit and one 10-bit index pair per block,
// the 2 sparsity bits above the indices, 2 activation PCs at every shape, one
// 16-bit result per block), so nothing else in this file depends on the shape.
#ifndef CORES
#define CORES 4
#endif
#ifndef BLOCKS
#define BLOCKS 4
#endif
static const int PC_BYTES = 32;    // 256-bit pseudo-channel beat
static const int LANES = CORES * BLOCKS;
static const int W_PCS = (32 * LANES + 255) / 256;
static const int IND_PCS = (10 * LANES + 2 + 255) / 256;
static const int A_PCS = 2;
static const int C_PCS = (16 * LANES + 255) / 256;
static const int WIN_ELEMS = 32;   // activation elements per window
static const size_t ALIGN = 4096;  // XRT DMA alignment

// Where the 2-bit sparsity code sits in the joined index word: bit IND_BITS.
// Derived, never hardcoded: 4x4 puts it at index PC 0 byte 20, 8x4 at PC 1 byte 8,
// 8x8 at PC 2 byte 16 (the position the single-tenant host hardcodes).
static const int IND_BITS = 10 * LANES;                 // 160
static const int SP_PC    = IND_BITS / 256;             // 0
static const int SP_BYTE  = (IND_BITS % 256) / 8;       // 20

static const int PCS_PER_TENANT = W_PCS + IND_PCS + A_PCS + C_PCS;   // 6

static const char* SP_NAME[4] = { "2:4", "2:8", "2:16", "2:32" };
static inline int sp_M(unsigned code)      { return 4 << code; }         // 4/8/16/32
static inline int sp_freeze(unsigned code) { return 32 / sp_M(code); }   // 8/4/2/1

#define OCL_CHECK(err, expr)                                                   \
    do {                                                                       \
        (expr);                                                                \
        if ((err) != CL_SUCCESS) {                                             \
            std::fprintf(stderr, "OpenCL error %d at %s:%d\n", (int)(err),     \
                         __FILE__, __LINE__);                                  \
            std::exit(1);                                                      \
        }                                                                      \
    } while (0)

// ---- 4096-byte aligned buffer (verbatim from host_sparse.cpp) --------------
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

// Span from the earliest START to the latest END across events. Summing per-CU
// durations would count concurrent work several times over.
static void event_span(const std::vector<cl::Event>& evs,
                       cl_ulong& first_start, cl_ulong& last_end) {
    first_start = ~(cl_ulong)0;
    last_end = 0;
    for (size_t i = 0; i < evs.size(); ++i) {
        cl_ulong s = evs[i].getProfilingInfo<CL_PROFILING_COMMAND_START>();
        cl_ulong e = evs[i].getProfilingInfo<CL_PROFILING_COMMAND_END>();
        first_start = std::min(first_start, s);
        last_end = std::max(last_end, e);
    }
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
            std::printf("  found device: %s\n", dname.c_str());
            if (dname.find("u280") != std::string::npos ||
                dname.find("U280") != std::string::npos) {
                std::printf("  selected    : %s\n", dname.c_str());
                return devs[d];
            }
        }
    }
    std::fprintf(stderr, "no U280 found -- refusing to run on the wrong card\n");
    std::exit(1);
}

// ---------------------------------------------------------------------------
// One tenant: its data, its CUs, its queue, its results.
// ---------------------------------------------------------------------------
struct Tenant {
    int id;                        // the k in gemv_tk -- picks the CUs and the PCs
    std::string dir;               // stimulus directory (bin/, golden.txt, output.txt)

    std::vector<AlignedBuf> w_img, i_img, a_img, c_img;
    size_t n_weight_beats, n_act_beats, n_out_beats;
    std::vector<unsigned> lap_code;
    std::vector<size_t> lap_beats;

    std::vector<cl::Kernel> k_w, k_i, k_a, k_c;
    std::vector<cl::Buffer> b_w, b_i, b_a, b_c;
    std::vector<cl::Memory> to_dev, from_dev;
    cl::CommandQueue q;
    std::vector<cl::Event> ev_k;

    // ns, device profiling clock.
    //   t_start  = earliest START among this tenant's movers  -> the SPAN
    //   t_active = latest START among its INPUT movers        -> the ACTIVE span
    //   t_end    = latest END among its movers
    // The barrier join cannot consume a beat until EVERY input stream is flowing, so
    // the engine really starts working at t_active. The span also contains the
    // host's enqueue stagger (~44 us per CU, and more on a loaded host); the active
    // span mostly does not, which makes it the right basis for interference.
    cl_ulong t_start, t_active, t_end;
    size_t soak_iters;

    Tenant() : id(0), w_img(W_PCS), i_img(IND_PCS), a_img(A_PCS), c_img(C_PCS),
               n_weight_beats(0), n_act_beats(0), n_out_beats(0),
               k_w(W_PCS), k_i(IND_PCS), k_a(A_PCS), k_c(C_PCS),
               b_w(W_PCS), b_i(IND_PCS), b_a(A_PCS), b_c(C_PCS),
               t_start(0), t_active(0), t_end(0), soak_iters(0) {}

    std::string tag() const { return "t" + std::to_string(id); }
    size_t rows() const { return n_out_beats * (size_t)LANES; }
    double span_us() const { return (double)(t_end - t_start) / 1e3; }
    double active_us() const { return (double)(t_end - t_active) / 1e3; }
};

// Read the per-PC images and walk the laps -- the single-tenant logic, per tenant.
static void load_stimulus(Tenant& T) {
    const std::string bindir = T.dir + "/bin";
    for (int i = 0; i < W_PCS; ++i)
        read_into(bindir + "/weights_pc" + std::to_string(i) + ".bin", T.w_img[i]);
    for (int i = 0; i < IND_PCS; ++i)
        read_into(bindir + "/ind_pc" + std::to_string(i) + ".bin", T.i_img[i]);
    for (int i = 0; i < A_PCS; ++i)
        read_into(bindir + "/act_pc" + std::to_string(i) + ".bin", T.a_img[i]);

    T.n_weight_beats = T.w_img[0].n / PC_BYTES;
    const size_t n_index_beats = T.i_img[0].n / PC_BYTES;
    T.n_act_beats = T.a_img[0].n / PC_BYTES;

    // Weights and indices are joined beat-for-beat by the barrier join: a short
    // index image does not give wrong numbers, it HANGS the engine.
    if (n_index_beats != T.n_weight_beats) {
        std::fprintf(stderr, "%s: %zu index beats vs %zu weight beats -- they are "
                     "joined beat-for-beat; a mismatch hangs the engine.\n",
                     T.tag().c_str(), n_index_beats, T.n_weight_beats);
        std::exit(1);
    }
    if (T.n_act_beats == 0) {
        std::fprintf(stderr, "%s: act_pc0.bin is empty\n", T.tag().c_str());
        std::exit(1);
    }

    size_t pos = 0;
    while (pos < T.n_weight_beats) {
        const unsigned code = sparsity_at(T.i_img[SP_PC], pos);
        const size_t nb = T.n_act_beats * (size_t)sp_freeze(code);
        if (pos + nb > T.n_weight_beats) {
            std::fprintf(stderr, "%s: stimulus truncated -- lap %zu at %s needs %zu "
                         "beats, %zu remain. Regenerate.\n", T.tag().c_str(),
                         T.lap_code.size(), SP_NAME[code], nb, T.n_weight_beats - pos);
            std::exit(1);
        }
        for (size_t k = pos; k < pos + nb; ++k) {
            if (sparsity_at(T.i_img[SP_PC], k) != code) {
                std::fprintf(stderr, "%s: sparsity code changes mid-lap at beat %zu "
                             "(lap %zu started as %s) -- packing bug.\n",
                             T.tag().c_str(), k, T.lap_code.size(), SP_NAME[code]);
                std::exit(1);
            }
        }
        T.lap_code.push_back(code);
        T.lap_beats.push_back(nb);
        pos += nb;
    }

    T.n_out_beats = T.lap_code.size();
    for (int i = 0; i < C_PCS; ++i) T.c_img[i].alloc(T.n_out_beats * PC_BYTES);

    // Printed in the SAME wording the single-tenant host uses, with a tenant
    // prefix, so the existing rows/beats guards can be reused per tenant.
    std::printf("%s stimulus: %zu weight beats (= index beats), %zu activation beats (V=%zu)\n",
                T.tag().c_str(), T.n_weight_beats, T.n_act_beats,
                T.n_act_beats * WIN_ELEMS);
    std::printf("%s           %zu lap(s) -> %zu output beats = %zu rows\n",
                T.tag().c_str(), T.n_out_beats, T.n_out_beats, T.rows());
    {
        size_t L = 0;
        while (L < T.lap_code.size()) {
            const unsigned code = T.lap_code[L];
            size_t n = 0, beats = 0;
            while (L + n < T.lap_code.size() && T.lap_code[L + n] == code) {
                beats += T.lap_beats[L + n];
                ++n;
            }
            std::printf("%s           laps %5zu..%-5zu %-5s freeze %d cyc/window  %zu beats\n",
                        T.tag().c_str(), L, L + n - 1, SP_NAME[code],
                        sp_freeze(code), beats);
            L += n;
        }
    }
}

static void bind_tenant(Tenant& T, cl::Program& program, cl::Context& context,
                        cl::Device& device) {
    cl_int err = CL_SUCCESS;
    const std::string s = "_" + T.tag();

    // OUT-OF-ORDER is load-bearing: on an in-order queue this tenant's movers
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
    for (int i = 0; i < A_PCS; ++i)
        OCL_CHECK(err, T.k_a[i] = cl::Kernel(program,
                  ("krnl_mm2s:{mm2s_a" + std::to_string(i) + s + "}").c_str(), &err));
    for (int i = 0; i < C_PCS; ++i)
        OCL_CHECK(err, T.k_c[i] = cl::Kernel(program,
                  ("krnl_s2mm:{s2mm_c" + std::to_string(i) + s + "}").c_str(), &err));
    // gemv_tk is free-running (ap_ctrl_none): no handle, no args, no enqueue.

    for (int i = 0; i < W_PCS; ++i) {
        OCL_CHECK(err, T.b_w[i] = cl::Buffer(context, CL_MEM_USE_HOST_PTR | CL_MEM_READ_ONLY,
                                             T.w_img[i].n, T.w_img[i].p, &err));
        T.k_w[i].setArg(0, T.b_w[i]);
        T.k_w[i].setArg(2, (unsigned)T.n_weight_beats);   // arg 1 is the STREAM
        T.to_dev.push_back(T.b_w[i]);
    }
    for (int i = 0; i < IND_PCS; ++i) {
        OCL_CHECK(err, T.b_i[i] = cl::Buffer(context, CL_MEM_USE_HOST_PTR | CL_MEM_READ_ONLY,
                                             T.i_img[i].n, T.i_img[i].p, &err));
        T.k_i[i].setArg(0, T.b_i[i]);
        T.k_i[i].setArg(2, (unsigned)T.n_weight_beats);   // joined with the weights
        T.to_dev.push_back(T.b_i[i]);
    }
    for (int i = 0; i < A_PCS; ++i) {
        OCL_CHECK(err, T.b_a[i] = cl::Buffer(context, CL_MEM_USE_HOST_PTR | CL_MEM_READ_ONLY,
                                             T.a_img[i].n, T.a_img[i].p, &err));
        T.k_a[i].setArg(0, T.b_a[i]);
        T.k_a[i].setArg(2, (unsigned)T.n_act_beats);
        T.to_dev.push_back(T.b_a[i]);
    }
    for (int i = 0; i < C_PCS; ++i) {
        OCL_CHECK(err, T.b_c[i] = cl::Buffer(context, CL_MEM_USE_HOST_PTR | CL_MEM_WRITE_ONLY,
                                             T.c_img[i].n, T.c_img[i].p, &err));
        T.k_c[i].setArg(1, T.b_c[i]);                     // arg 0 is the STREAM
        T.k_c[i].setArg(2, (unsigned)T.n_out_beats);
        T.from_dev.push_back(T.b_c[i]);
    }
}

// Enqueue every mover of one tenant, newest consumer first, WITHOUT waiting.
// Output movers go first so the sink is ready before the sources push.
static void enqueue_tenant(Tenant& T, bool keep_events) {
    cl_int err = CL_SUCCESS;
    if (keep_events) T.ev_k.clear();
    for (int i = 0; i < C_PCS; ++i) {
        cl::Event e; OCL_CHECK(err, err = T.q.enqueueTask(T.k_c[i], NULL, &e));
        if (keep_events) T.ev_k.push_back(e);
    }
    for (int i = 0; i < A_PCS; ++i) {
        cl::Event e; OCL_CHECK(err, err = T.q.enqueueTask(T.k_a[i], NULL, &e));
        if (keep_events) T.ev_k.push_back(e);
    }
    for (int i = 0; i < IND_PCS; ++i) {
        cl::Event e; OCL_CHECK(err, err = T.q.enqueueTask(T.k_i[i], NULL, &e));
        if (keep_events) T.ev_k.push_back(e);
    }
    for (int i = 0; i < W_PCS; ++i) {
        cl::Event e; OCL_CHECK(err, err = T.q.enqueueTask(T.k_w[i], NULL, &e));
        if (keep_events) T.ev_k.push_back(e);
    }
}

static void write_output(const Tenant& T) {
    const std::string outpath = T.dir + "/output.txt";
    std::FILE* of = std::fopen(outpath.c_str(), "w");
    if (!of) {
        std::fprintf(stderr, "cannot write %s\n", outpath.c_str());
        std::exit(1);
    }
    for (size_t k = 0; k < T.n_out_beats; ++k) {
        for (int r = 0; r < LANES; ++r) {
            const int pc = r / 16;               // 16 bf16 elements per 256-bit beat
            const int lane_in_pc = r % 16;
            const unsigned char* p = &T.c_img[pc].p[k * PC_BYTES + lane_in_pc * 2];
            unsigned v = (unsigned)p[0] | ((unsigned)p[1] << 8);
            std::fprintf(of, "%04X\n", v);
        }
    }
    std::fclose(of);
    std::printf("%s wrote %s  (%zu rows)\n", T.tag().c_str(), outpath.c_str(), T.rows());
}

int main(int argc, char** argv) {
    if (argc < 5) {
        std::fprintf(stderr,
            "usage: %s <xclbin> <clock_MHz> <soak_seconds> [--lockstep] <k:dir> [<k:dir> ...]\n"
            "  k:dir         tenant index (the k in gemv_tk) and its stimulus dir.\n"
            "                Pass ONE pair to run that tenant alone; pass all of\n"
            "                them to run them concurrently.\n"
            "  clock_MHz     the kernel clock the card RUNS at (xclbin DATA_CLK).\n"
            "  soak_seconds  0 = off. >0 soaks every tenant in its own thread.\n"
            "  --lockstep    soak the tenants as ONE job: launch all, wait for all.\n",
            argv[0]);
        return 1;
    }
    const std::string xclbin_path = argv[1];
    const double clock_mhz = atof(argv[2]);
    const double soak_seconds = atof(argv[3]);
    if (clock_mhz <= 0.0) {
        std::fprintf(stderr, "clock_MHz must be positive\n");
        return 1;
    }

    std::vector<std::unique_ptr<Tenant> > ts;
    bool lockstep = false;
    for (int a = 4; a < argc; ++a) {
        const std::string arg = argv[a];
        if (arg == "--lockstep") {
            lockstep = true;
            continue;
        }
        const size_t colon = arg.find(':');
        if (colon == std::string::npos || colon == 0 || colon + 1 >= arg.size()) {
            std::fprintf(stderr, "bad tenant spec '%s' -- expected k:dir\n", arg.c_str());
            return 1;
        }
        std::unique_ptr<Tenant> T(new Tenant());
        T->id = atoi(arg.substr(0, colon).c_str());
        T->dir = arg.substr(colon + 1);
        for (size_t j = 0; j < ts.size(); ++j) {
            if (ts[j]->id == T->id) {
                std::fprintf(stderr, "tenant %d given twice\n", T->id);
                return 1;
            }
        }
        ts.push_back(std::move(T));
    }
    if (ts.empty()) {
        std::fprintf(stderr, "no k:dir tenant given\n");
        return 1;
    }

    // DELETE EVERY TENANT'S OLD output.txt BEFORE ANYTHING CAN FAIL. A run that dies
    // early (no XRT environment, wrong xclbin, a missing CU) must not leave a previous
    // run's result behind for the compare to find: on 2026-09-21 an aborted host plus
    // `... ; compare` produced a PASS against a stale hw_emu output.
    for (size_t i = 0; i < ts.size(); ++i)
        std::remove((ts[i]->dir + "/output.txt").c_str());

    // Without the XRT environment the runtime aborts with SIGABRT (exit 134) during setup,
    // which reads like a crash in this program. Say what is wrong instead.
    if (!std::getenv("XILINX_XRT")) {
        std::fprintf(stderr, "XILINX_XRT is not set -- run: source /opt/xilinx/xrt/setup.sh\n");
        return 1;
    }

    std::printf("multi-tenant run: %zu tenant(s), 4x4 each, %d HBM channels per tenant\n",
                ts.size(), PCS_PER_TENANT);
    for (size_t i = 0; i < ts.size(); ++i)
        std::printf("  tenant %d  CUs *_t%d  HBM[%d..%d]  dir %s\n",
                    ts[i]->id, ts[i]->id, ts[i]->id * PCS_PER_TENANT,
                    ts[i]->id * PCS_PER_TENANT + PCS_PER_TENANT - 1, ts[i]->dir.c_str());

    for (size_t i = 0; i < ts.size(); ++i) load_stimulus(*ts[i]);

    cl_int err = CL_SUCCESS;
    cl::Device device = pick_u280();
    cl::Context context(device, NULL, NULL, NULL, &err);
    std::vector<unsigned char> bits = read_file(xclbin_path);
    cl::Program::Binaries bins;
    bins.push_back(std::make_pair((const void*)bits.data(), bits.size()));
    std::vector<cl::Device> devs(1, device);
    cl::Program program(context, devs, bins, NULL, &err);
    std::printf("xclbin loaded: %s\n", xclbin_path.c_str());

    for (size_t i = 0; i < ts.size(); ++i) bind_tenant(*ts[i], program, context, device);

    // ---- host -> device, all tenants, then wait ----------------------------
    for (size_t i = 0; i < ts.size(); ++i)
        OCL_CHECK(err, err = ts[i]->q.enqueueMigrateMemObjects(ts[i]->to_dev, 0));
    for (size_t i = 0; i < ts.size(); ++i) ts[i]->q.finish();

    // ---- launch every tenant, THEN wait ------------------------------------
    // Enqueueing all tenants before any finish() is what makes them concurrent;
    // finishing one at a time would measure them nearly serially.
    const double t_launch = now_epoch();
    for (size_t i = 0; i < ts.size(); ++i) enqueue_tenant(*ts[i], true);
    std::printf("launched %zu CUs across %zu tenant(s), waiting...\n",
                ts.size() * (size_t)(PCS_PER_TENANT), ts.size());
    for (size_t i = 0; i < ts.size(); ++i) ts[i]->q.finish();
    const double t_done = now_epoch();

    for (size_t i = 0; i < ts.size(); ++i) {
        Tenant& T = *ts[i];
        event_span(T.ev_k, T.t_start, T.t_end);
        // ev_k is in enqueue order: the C_PCS OUTPUT movers first, then every input
        // mover (activations, indices, weights). The latest input START is when the
        // join can first run.
        T.t_active = 0;
        for (size_t j = (size_t)C_PCS; j < T.ev_k.size(); ++j)
            T.t_active = std::max(T.t_active,
                                  T.ev_k[j].getProfilingInfo<CL_PROFILING_COMMAND_START>());
    }

    // ---- POWER SOAK (optional) ---------------------------------------------
    // One THREAD per tenant so tenants are genuinely independent: a round-robin
    // loop in one thread would make every tenant wait for the slowest, which is
    // the opposite of the property under test. --lockstep deliberately does the
    // opposite, for the SHARED workload, where waiting for the slowest IS the job.
    double soak_t0 = 0.0, soak_t1 = 0.0;
    if (soak_seconds > 0.0) {
        std::printf("\n--- power soak: %.1f s, %zu tenant(s), buffers resident, compute only, %s ---\n",
                    soak_seconds, ts.size(),
                    lockstep ? "LOCKSTEP (launch all, wait for all)" : "one thread per tenant");
        soak_t0 = now_epoch();
        std::printf("SOAK_START_EPOCH %.3f\n", soak_t0);
        std::fflush(stdout);

        if (lockstep) {
            // ONE job per iteration: every tenant's share of the same work is launched,
            // then ALL of them are awaited. The fast tenants sit idle until the slowest
            // finishes -- exactly what they do in the shared workload being measured.
            while (now_epoch() - soak_t0 < soak_seconds) {
                for (size_t i = 0; i < ts.size(); ++i) enqueue_tenant(*ts[i], false);
                for (size_t i = 0; i < ts.size(); ++i) ts[i]->q.finish();
                for (size_t i = 0; i < ts.size(); ++i) ++ts[i]->soak_iters;
            }
        } else {
            std::vector<std::thread> workers;
            for (size_t i = 0; i < ts.size(); ++i) {
                Tenant* T = ts[i].get();
                workers.push_back(std::thread([T, soak_t0, soak_seconds]() {
                    while (now_epoch() - soak_t0 < soak_seconds) {
                        enqueue_tenant(*T, false);
                        T->q.finish();
                        ++T->soak_iters;
                    }
                }));
            }
            for (size_t i = 0; i < workers.size(); ++i) workers[i].join();
        }

        soak_t1 = now_epoch();
        std::printf("SOAK_END_EPOCH %.3f\n", soak_t1);
        const double window = soak_t1 - soak_t0;
        double agg_rows = 0.0;
        for (size_t i = 0; i < ts.size(); ++i) {
            Tenant& T = *ts[i];
            const double rows = (double)T.soak_iters * T.rows();
            agg_rows += rows;
            std::printf("%s soak: %zu calculations in %.3f s -> %.3f Mrow/s sustained\n",
                        T.tag().c_str(), T.soak_iters, window, rows / window / 1e6);
        }
        std::printf("soak AGGREGATE: %.3f Mrow/s sustained across %zu tenant(s)\n",
                    agg_rows / window / 1e6, ts.size());
        std::fflush(stdout);
    }

    // ---- device -> host ----------------------------------------------------
    // After a soak this reads back the LAST iteration, so the compare proves the
    // engine is still bit-exact after thousands of consecutive calculations.
    for (size_t i = 0; i < ts.size(); ++i)
        OCL_CHECK(err, err = ts[i]->q.enqueueMigrateMemObjects(ts[i]->from_dev,
                                                              CL_MIGRATE_MEM_OBJECT_HOST));
    for (size_t i = 0; i < ts.size(); ++i) ts[i]->q.finish();

    // ---- per-tenant measurements -------------------------------------------
    std::printf("\n--- per-tenant results (OpenCL profiling events; no H2D/D2H) ---\n");
    // Columns 2-7 are unchanged in meaning (all on the SPAN). The last two are new:
    // the ACTIVE span and the efficiency computed on it.
    std::printf("  %-4s %10s %10s %12s %12s %10s %8s %10s %9s\n",
                "tenant", "span us", "rows", "Mrow/s", "GMAC/s", "beats/cyc", "eff %",
                "active us", "act eff%");
    for (size_t i = 0; i < ts.size(); ++i) {
        Tenant& T = *ts[i];
        const double ns = (double)(T.t_end - T.t_start);
        const double act_ns = (double)(T.t_end - T.t_active);
        const double macs = (double)T.n_weight_beats * LANES * 2.0;
        const double ideal_ns = (double)T.n_weight_beats * (1000.0 / clock_mhz);
        std::printf("  %-4s %10.3f %10zu %12.3f %12.3f %10.3f %8.1f %10.3f %9.1f\n",
                    T.tag().c_str(), ns / 1e3, T.rows(), T.rows() / ns * 1e3,
                    macs / ns, ideal_ns / ns, ideal_ns / ns * 100.0,
                    act_ns / 1e3, act_ns > 0 ? ideal_ns / act_ns * 100.0 : 0.0);
    }

    // ---- aggregate, and how much of it was genuinely concurrent ------------
    if (ts.size() > 1) {
        cl_ulong first_start = ~(cl_ulong)0, last_end = 0;
        cl_ulong last_start = 0, first_end = ~(cl_ulong)0;
        size_t rows_total = 0;
        double macs_total = 0.0;
        for (size_t i = 0; i < ts.size(); ++i) {
            first_start = std::min(first_start, ts[i]->t_start);
            last_end = std::max(last_end, ts[i]->t_end);
            last_start = std::max(last_start, ts[i]->t_start);
            first_end = std::min(first_end, ts[i]->t_end);
            rows_total += ts[i]->rows();
            macs_total += (double)ts[i]->n_weight_beats * LANES * 2.0;
        }
        const double union_ns = (double)(last_end - first_start);
        const double overlap_ns = (first_end > last_start)
                                ? (double)(first_end - last_start) : 0.0;
        // The same overlap on ACTIVE windows: last active start -> first end, as a
        // share of the longest active span. This is the fraction of each engine's
        // real work that happened while every other engine was also working.
        cl_ulong last_active = 0;
        double longest_active = 0.0;
        for (size_t i = 0; i < ts.size(); ++i) {
            last_active = std::max(last_active, ts[i]->t_active);
            longest_active = std::max(longest_active,
                                      (double)(ts[i]->t_end - ts[i]->t_active));
        }
        const double act_overlap_ns = (first_end > last_active)
                                    ? (double)(first_end - last_active) : 0.0;
        std::printf("\n--- aggregate over %zu tenants ---\n", ts.size());
        std::printf("  union window     : %10.3f us  (first start -> last end)\n",
                    union_ns / 1e3);
        std::printf("  overlap window   : %10.3f us  (%.1f%% of the union -- the part\n"
                    "                                  where EVERY tenant was running)\n",
                    overlap_ns / 1e3, union_ns > 0 ? 100.0 * overlap_ns / union_ns : 0.0);
        std::printf("  active overlap   : %10.3f us  (%.1f%% of the longest ACTIVE span --\n"
                    "                                  engines working at the same time)\n",
                    act_overlap_ns / 1e3,
                    longest_active > 0 ? 100.0 * act_overlap_ns / longest_active : 0.0);
        std::printf("  aggregate rows   : %10zu -> %.3f Mrow/s over the union window\n",
                    rows_total, rows_total / union_ns * 1e3);
        std::printf("  aggregate MACs   : %10.3f GMAC/s over the union window\n",
                    macs_total / union_ns);
        std::printf("  host wall clock  : %10.3f us  (launch -> all queues drained)\n",
                    (t_done - t_launch) * 1e6);
        if (overlap_ns <= 0.0)
            std::printf("  !! NO OVERLAP: the tenants did not run at the same time. Any\n"
                        "     interference number from this run is meaningless.\n");
        std::printf("\n  Interference is measured by COMPARING RUNS, not within one run:\n"
                    "  run each tenant alone (one k:dir), then all together, and divide\n"
                    "  the per-tenant spans.\n");
    }

    for (size_t i = 0; i < ts.size(); ++i) write_output(*ts[i]);
    std::printf("\ncompare each tenant against ITS OWN golden:\n");
    for (size_t i = 0; i < ts.size(); ++i)
        std::printf("  cd %s && rm -f tlast.txt && python3 compare_gemv4_py36.py\n",
                    ts[i]->dir.c_str());
    return 0;
}
