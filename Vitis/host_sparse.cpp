// ---------------------------------------------------------------------------
// host_sparse.cpp -- OpenCL host for the 2:M SPARSE GEMV kernel system.
//
//   13 x krnl_mm2s  ->  krnl_gemv_sparse (free-running)  ->  4 x krnl_s2mm
//
// Same shape as the dense host (Vitis/host.cpp), plus the 3 index movers. The
// mover kernels are UNCHANGED and are not rebuilt -- an index PC is just
// another 256-bit stream to them.
//
// The compute kernel is FREE-RUNNING (ap_ctrl_none): it has no control
// interface, is never referenced here, and starts when the xclbin is
// programmed. Its stream ports take no setArg.
//
// ---- THE ONE REAL DIFFERENCE FROM DENSE ----------------------------------
// Dense derives beats_per_lap = n_act_beats * 16, a constant, because its
// freeze is always 16 cycles per 32-element window. Sparse freezes for 32/M
// cycles, and M is NOT a host argument -- it rides in the index data, and in a
// runtime-reconfiguration run it CHANGES BETWEEN LAPS. So the number of output
// beats cannot be obtained by dividing.
//
// Instead the host reads the sparsity codes straight out of ind_pc2.bin and
// walks the laps exactly as the engine will:
//
//     code at beat p  ->  M = 4 << code  ->  this lap is n_act_beats * (32/M)
//     beats long; advance and repeat until the weight stream is consumed.
//
// This mirrors gemv4_cosim_gen.py's beat_meta construction, so host and golden
// model cannot disagree about lap boundaries. It also means the host PRINTS the
// sparsity schedule before running, which is the cheapest possible check that
// the stimulus on the card is the stimulus you think it is.
//
// SPARSITY LOCATION: bits [641:640] of the joined 768-bit index word. Index PC2
// covers joined bits [767:512], so within PC2 the code is at [129:128] -- byte
// 16 of each 32-byte beat, low 2 bits. See hex_to_bin.py sparsity_of_beat().
//
// Sizes are otherwise DERIVED from the image files, so the same binary runs the
// small emulation case and the full ladder:
//     n_weight_beats = weights_pc0.bin / 32   (= index beats; they are joined)
//     n_act_beats    = act_pc0.bin     / 32   (= V/32 = windows)
//
// Writes output.txt in golden.txt's format, so compare_gemv4_py36.py runs
// unchanged -- the same golden model from RTL simulation through to hardware.
//
// ---- MEASUREMENT NOTES ----------------------------------------------------
// The U280 is SHARED. Other users load their own xclbins, so load_xclbin here
// usually triggers a full FPGA reconfiguration -- seconds, and however long
// depends on what the previous user left loaded. Therefore:
//
//   * timing comes from OpenCL PROFILING EVENTS on the kernels, never from
//     wall clock around the program;
//   * host->device and device->host migrations are timed separately, so PCIe
//     transfer is never confused with compute;
//   * buffers are 4096-byte aligned (posix_memalign), so XRT DMAs straight
//     from them instead of staging through an extra memcpy.
//
// Back-to-back runs are cheap: XRT skips reconfiguration when the requested
// xclbin UUID is already loaded. Run several times, take the BEST kernel span.
//
// BUILD (server):
//   g++ -Wall -O2 -std=c++14 -I$XILINX_XRT/include host_sparse.cpp
//       -L$XILINX_XRT/lib -lOpenCL -lrt -lstdc++ -pthread -o host_sparse
//   (one line; a trailing backslash inside a // comment trips -Wcomment)
//
// RUN:
//   hw_emu : XCL_EMULATION_MODE=hw_emu ./host_sparse gemv_sparse.hw_emu.xclbin <emu_dir>
//   hw     : ./host_sparse gemv_sparse.xclbin <emu_dir> [clock_MHz] [soak_seconds]
//
// CLOCK_MHZ (optional 3rd arg, default 300) is the frequency the .xclbin was
// LINKED at -- v++ --kernel_frequency. It affects only the derived beats/cycle
// and efficiency figures, but getting it wrong silently scales the headline
// number: the sparse build closes at 225 MHz, and assuming 300 would understate
// beats/cycle by 25%. Dense = 300, sparse = 225 (or whatever it closed at).
//
// SOAK_SECONDS (optional 4th arg, default 0 = off) drives POWER MEASUREMENT.
// One calculation lasts ~10-80 ms, but `xbutil examine --report electrical`
// takes about a second to execute -- you cannot sample a 10 ms window with a
// 1 Hz instrument. Worse, a single invocation is ~85% H2D transfer (74.7 ms of
// DMA against a 10.2 ms kernel at the largest size), so sampling a plain run
// would mostly measure the DMA engine, not the compute.
//
// With soak_seconds > 0 the host re-enqueues all 17 CUs back-to-back until that
// much wall clock has elapsed, with the buffers ALREADY RESIDENT on the card --
// no H2D between iterations. That gives a clean, sustained, compute-only load
// window for an external sampler to average over. The window's start and end are
// printed as epoch seconds so samples can be clipped to exactly the load.
//
// It doubles as the strongest available test of the end-of-calculation teardown:
// the rerun testbench proved 2 consecutive calculations and hardware proved 2;
// a 60 s soak is thousands. The output is read back AFTER the soak, so
// compare_gemv4_py36.py verifies the LAST iteration is still bit-exact.
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
#include <ctime>       // clock_gettime / struct timespec, for the power soak window
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

static const int PC_BYTES = 32;   // 256-bit pseudo-channel beat
static const int W_PCS = 8;
static const int IND_PCS = 3;
static const int A_PCS = 2;
static const int C_PCS = 4;
static const int LANES = 64;      // 8 cores x 8 blocks
static const int WIN_ELEMS = 32;  // activation elements per window
static const size_t ALIGN = 4096; // XRT DMA alignment

// Sparsity code -> M, and -> accumulate cycles per 32-element window (32/M).
static const char* SP_NAME[4] = { "2:4", "2:8", "2:16", "2:32" };
static inline int sp_M(unsigned code)      { return 4 << code; }          // 4/8/16/32
static inline int sp_freeze(unsigned code) { return 32 / sp_M(code); }    // 8/4/2/1

#define OCL_CHECK(err, expr)                                                   \
    do {                                                                       \
        (expr);                                                                \
        if ((err) != CL_SUCCESS) {                                             \
            std::fprintf(stderr, "OpenCL error %d at %s:%d\n", (int)(err),     \
                         __FILE__, __LINE__);                                  \
            std::exit(1);                                                      \
        }                                                                      \
    } while (0)

// ---- 4096-byte aligned buffer ---------------------------------------------
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

// The 2-bit sparsity code carried by index beat k.
// Joined bit 640 lives in PC2 at bit 128 = byte 16, low 2 bits.
static inline unsigned sparsity_at(const AlignedBuf& ind_pc2, size_t k) {
    return (unsigned)(ind_pc2.p[k * PC_BYTES + 16] & 0x3);
}

// Span in nanoseconds from the earliest START to the latest END across events.
// With 17 concurrent CUs that span IS the compute phase; summing per-CU
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

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr,
            "usage: %s <xclbin> <emulation_dir> [clock_MHz] [soak_seconds]\n"
            "  clock_MHz    : what the xclbin was LINKED at "
            "(default 300; the sparse build closes at 225)\n"
            "  soak_seconds : 0 = off (default). >0 re-runs the kernels "
            "back-to-back for that long,\n"
            "                 buffers resident, for POWER measurement. "
            "Prints the load window as\n"
            "                 epoch seconds so a sampler can clip to it.\n",
            argv[0]);
        return 1;
    }
    const std::string xclbin_path = argv[1];
    const std::string emu = argv[2];
    const std::string bindir = emu + "/bin";
    // The clock this xclbin was LINKED at -- NOT a request. See the header.
    const double clock_mhz = (argc > 3) ? atof(argv[3]) : 300.0;
    if (clock_mhz <= 0.0) {
        std::fprintf(stderr, "clock_MHz must be positive\n");
        return 1;
    }
    // Wall-clock DURATION, not an iteration count: one iteration is ~10 ms at
    // 2:32 and ~80 ms at 2:4, so a fixed count would give wildly different
    // measurement windows per sparsity and the power averages would not be
    // comparable. A duration gives every configuration the same window.
    const double soak_seconds = (argc > 4) ? atof(argv[4]) : 0.0;

    cl_int err = CL_SUCCESS;

    // ---- load the per-PC images --------------------------------------------
    std::vector<AlignedBuf> w_img(W_PCS), i_img(IND_PCS), a_img(A_PCS), c_img(C_PCS);
    for (int i = 0; i < W_PCS; ++i)
        read_into(bindir + "/weights_pc" + std::to_string(i) + ".bin", w_img[i]);
    for (int i = 0; i < IND_PCS; ++i)
        read_into(bindir + "/ind_pc" + std::to_string(i) + ".bin", i_img[i]);
    for (int i = 0; i < A_PCS; ++i)
        read_into(bindir + "/act_pc" + std::to_string(i) + ".bin", a_img[i]);

    const size_t n_weight_beats = w_img[0].n / PC_BYTES;
    const size_t n_index_beats  = i_img[0].n / PC_BYTES;
    const size_t n_act_beats    = a_img[0].n / PC_BYTES;   // = windows = V/32

    // Weights and indices are joined beat-for-beat by the weights FIFO: the
    // barrier join waits for all 11 PCs, so a short index image does not
    // produce wrong numbers, it HANGS the engine. Catch it here instead.
    if (n_index_beats != n_weight_beats) {
        std::fprintf(stderr,
            "stimulus mismatch: %zu index beats vs %zu weight beats.\n"
            "They are joined beat-for-beat; a mismatch hangs the engine.\n",
            n_index_beats, n_weight_beats);
        return 1;
    }
    if (n_act_beats == 0) {
        std::fprintf(stderr, "act_pc0.bin is empty\n");
        return 1;
    }

    // ---- walk the laps, reading the sparsity out of the index data ---------
    // Exactly the engine's own accounting: a lap is n_act_beats windows, each
    // held for 32/M cycles, and M comes from that lap's index beats.
    std::vector<unsigned> lap_code;
    std::vector<size_t> lap_beats;
    {
        size_t pos = 0;
        while (pos < n_weight_beats) {
            const unsigned code = sparsity_at(i_img[2], pos);
            const size_t nb = n_act_beats * (size_t)sp_freeze(code);
            if (pos + nb > n_weight_beats) {
                std::fprintf(stderr,
                    "stimulus truncated: lap %zu at sparsity %s needs %zu beats "
                    "but only %zu remain. Regenerate the stimulus.\n",
                    lap_code.size(), SP_NAME[code], nb, n_weight_beats - pos);
                return 1;
            }
            // Every beat of a lap must carry the same code -- the engine
            // re-samples at each window load, so a stray code mid-lap would
            // change the freeze under the accumulator and silently corrupt
            // that row. Cheaper to catch on the host than to debug on the card.
            for (size_t k = pos; k < pos + nb; ++k) {
                if (sparsity_at(i_img[2], k) != code) {
                    std::fprintf(stderr,
                        "sparsity code changes mid-lap at beat %zu (lap %zu started "
                        "as %s). Packing bug -- check hex_to_bin.py.\n",
                        k, lap_code.size(), SP_NAME[code]);
                    return 1;
                }
            }
            lap_code.push_back(code);
            lap_beats.push_back(nb);
            pos += nb;
        }
    }

    const size_t n_out_beats = lap_code.size();          // one flush per lap
    const size_t out_bytes   = n_out_beats * PC_BYTES;
    for (int i = 0; i < C_PCS; ++i) c_img[i].alloc(out_bytes);

    std::printf("stimulus: %zu weight beats (= index beats), %zu activation beats (V=%zu)\n",
                n_weight_beats, n_act_beats, n_act_beats * WIN_ELEMS);
    std::printf("          %zu lap(s) -> %zu output beats = %zu rows\n",
                n_out_beats, n_out_beats, n_out_beats * LANES);
    std::printf("sparsity schedule (read from ind_pc2 bits [129:128]):\n");
    // RUN-LENGTH COLLAPSED, not one line per lap. A correctness run has 4 laps;
    // a timing run has up to 65,536, and printing each one buries the output and
    // slows the sweep. Consecutive laps at the same sparsity are one line, which
    // is also the more readable form for a reconfiguration run -- what matters
    // there is the sequence of codes, not which lap index each change fell on.
    {
        size_t at = 0, L = 0;
        while (L < lap_code.size()) {
            const unsigned code = lap_code[L];
            size_t n = 0, beats = 0;
            while (L + n < lap_code.size() && lap_code[L + n] == code) {
                beats += lap_beats[L + n];
                ++n;
            }
            std::printf("  laps %6zu..%-6zu %-5s  freeze %d cyc/window  %zu beats  "
                        "(beats %zu..%zu)\n",
                        L, L + n - 1, SP_NAME[code], sp_freeze(code), beats,
                        at, at + beats - 1);
            at += beats;
            L += n;
        }
        bool mixed = false;
        for (size_t k = 1; k < lap_code.size(); ++k)
            if (lap_code[k] != lap_code[0]) mixed = true;
        if (mixed)
            std::printf("  -> MIXED: this is a RUNTIME-RECONFIGURATION run.\n");
    }

    // ---- device, context, queue --------------------------------------------
    cl::Device device = pick_u280();
    cl::Context context(device, NULL, NULL, NULL, &err);

    // OUT-OF-ORDER is load-bearing: on an in-order queue the 17 CUs serialise
    // and a producer waits on a consumer that cannot start -- deadlock.
    cl::CommandQueue q(context, device,
                       CL_QUEUE_PROFILING_ENABLE | CL_QUEUE_OUT_OF_ORDER_EXEC_MODE_ENABLE,
                       &err);

    std::vector<unsigned char> bits = read_file(xclbin_path);
    cl::Program::Binaries bins;
    bins.push_back(std::make_pair((const void*)bits.data(), bits.size()));
    std::vector<cl::Device> devs(1, device);
    cl::Program program(context, devs, bins, NULL, &err);
    std::printf("xclbin loaded: %s\n", xclbin_path.c_str());

    // ---- one kernel handle per COMPUTE UNIT --------------------------------
    // "krnl_mm2s:{mm2s_w0}" addresses one specific CU. A bare "krnl_mm2s" lets
    // the runtime pick any of the thirteen, each bound to a different HBM
    // channel -- wrong data, and no error anywhere. With weight and index
    // movers now indistinguishable except by name, this matters more than it
    // did at 10 CUs.
    // The CU names must match sparse_hbm.cfg's nk= line exactly.
    std::vector<cl::Kernel> k_w(W_PCS), k_i(IND_PCS), k_a(A_PCS), k_c(C_PCS);
    for (int i = 0; i < W_PCS; ++i)
        k_w[i] = cl::Kernel(program, ("krnl_mm2s:{mm2s_w" + std::to_string(i) + "}").c_str(), &err);
    for (int i = 0; i < IND_PCS; ++i)
        k_i[i] = cl::Kernel(program, ("krnl_mm2s:{mm2s_i" + std::to_string(i) + "}").c_str(), &err);
    for (int i = 0; i < A_PCS; ++i)
        k_a[i] = cl::Kernel(program, ("krnl_mm2s:{mm2s_a" + std::to_string(i) + "}").c_str(), &err);
    for (int i = 0; i < C_PCS; ++i)
        k_c[i] = cl::Kernel(program, ("krnl_s2mm:{s2mm_c" + std::to_string(i) + "}").c_str(), &err);
    // krnl_gemv_sparse: free-running, no handle, never started by the host.

    // ---- buffers -----------------------------------------------------------
    std::vector<cl::Buffer> b_w(W_PCS), b_i(IND_PCS), b_a(A_PCS), b_c(C_PCS);
    std::vector<cl::Memory> to_dev, from_dev;

    for (int i = 0; i < W_PCS; ++i) {
        b_w[i] = cl::Buffer(context, CL_MEM_USE_HOST_PTR | CL_MEM_READ_ONLY,
                            w_img[i].n, w_img[i].p, &err);
        k_w[i].setArg(0, b_w[i]);
        k_w[i].setArg(2, (unsigned)n_weight_beats);   // arg 1 is the STREAM -- never set
        to_dev.push_back(b_w[i]);
    }
    for (int i = 0; i < IND_PCS; ++i) {
        b_i[i] = cl::Buffer(context, CL_MEM_USE_HOST_PTR | CL_MEM_READ_ONLY,
                            i_img[i].n, i_img[i].p, &err);
        k_i[i].setArg(0, b_i[i]);
        k_i[i].setArg(2, (unsigned)n_weight_beats);   // same count -- joined streams
        to_dev.push_back(b_i[i]);
    }
    for (int i = 0; i < A_PCS; ++i) {
        b_a[i] = cl::Buffer(context, CL_MEM_USE_HOST_PTR | CL_MEM_READ_ONLY,
                            a_img[i].n, a_img[i].p, &err);
        k_a[i].setArg(0, b_a[i]);
        k_a[i].setArg(2, (unsigned)n_act_beats);
        to_dev.push_back(b_a[i]);
    }
    for (int i = 0; i < C_PCS; ++i) {
        b_c[i] = cl::Buffer(context, CL_MEM_USE_HOST_PTR | CL_MEM_WRITE_ONLY,
                            out_bytes, c_img[i].p, &err);
        k_c[i].setArg(1, b_c[i]);                     // arg 0 is the STREAM
        k_c[i].setArg(2, (unsigned)n_out_beats);
        from_dev.push_back(b_c[i]);
    }

    // ---- host -> device ----------------------------------------------------
    cl::Event ev_h2d;
    OCL_CHECK(err, err = q.enqueueMigrateMemObjects(to_dev, 0, NULL, &ev_h2d));
    q.finish();

    // ---- launch ------------------------------------------------------------
    // Order is irrelevant on an out-of-order queue: all 17 CUs run together.
    // Consumers first anyway, so the output path is ready before data flows.
    std::vector<cl::Event> ev_k;
    ev_k.reserve(W_PCS + IND_PCS + A_PCS + C_PCS);
    for (int i = 0; i < C_PCS; ++i)   { cl::Event e; OCL_CHECK(err, err = q.enqueueTask(k_c[i], NULL, &e)); ev_k.push_back(e); }
    for (int i = 0; i < A_PCS; ++i)   { cl::Event e; OCL_CHECK(err, err = q.enqueueTask(k_a[i], NULL, &e)); ev_k.push_back(e); }
    for (int i = 0; i < IND_PCS; ++i) { cl::Event e; OCL_CHECK(err, err = q.enqueueTask(k_i[i], NULL, &e)); ev_k.push_back(e); }
    for (int i = 0; i < W_PCS; ++i)   { cl::Event e; OCL_CHECK(err, err = q.enqueueTask(k_w[i], NULL, &e)); ev_k.push_back(e); }

    std::printf("launched %zu CUs, waiting...\n", ev_k.size());
    q.finish();
    std::printf("all CUs done\n");

    // ---- POWER SOAK (optional) ---------------------------------------------
    // Buffers stay resident: this loop enqueues ONLY the kernels, never a
    // migration, so the card is doing compute and HBM traffic and nothing else.
    // The OpenCL equivalent of the XRT-native run()/wait() pattern is
    // enqueueTask() + finish(), which is what one iteration is here.
    size_t soak_iters = 0;
    double soak_t0 = 0.0, soak_t1 = 0.0;
    if (soak_seconds > 0.0) {
        std::printf("\n--- power soak: %.1f s, buffers resident, compute only ---\n",
                    soak_seconds);
        struct timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        soak_t0 = ts.tv_sec + ts.tv_nsec / 1e9;
        std::printf("SOAK_START_EPOCH %.3f\n", soak_t0);
        std::fflush(stdout);   // the sampler may be reading our stdout live

        double now = soak_t0;
        while (now - soak_t0 < soak_seconds) {
            for (int i = 0; i < C_PCS; ++i)   OCL_CHECK(err, err = q.enqueueTask(k_c[i]));
            for (int i = 0; i < A_PCS; ++i)   OCL_CHECK(err, err = q.enqueueTask(k_a[i]));
            for (int i = 0; i < IND_PCS; ++i) OCL_CHECK(err, err = q.enqueueTask(k_i[i]));
            for (int i = 0; i < W_PCS; ++i)   OCL_CHECK(err, err = q.enqueueTask(k_w[i]));
            q.finish();
            ++soak_iters;
            clock_gettime(CLOCK_REALTIME, &ts);
            now = ts.tv_sec + ts.tv_nsec / 1e9;
        }
        soak_t1 = now;
        std::printf("SOAK_END_EPOCH %.3f\n", soak_t1);
        std::printf("soak: %zu consecutive calculations in %.3f s (%.3f ms each)\n",
                    soak_iters, soak_t1 - soak_t0,
                    (soak_t1 - soak_t0) * 1e3 / (double)soak_iters);
        std::printf("      %.0f rows total, %.3f Mrow/s sustained\n",
                    (double)soak_iters * n_out_beats * LANES,
                    (double)soak_iters * n_out_beats * LANES / (soak_t1 - soak_t0) / 1e6);
        std::fflush(stdout);
    }

    // ---- device -> host ----------------------------------------------------
    // After a soak this reads back the LAST iteration, so the usual compare
    // verifies the engine is still bit-exact after thousands of consecutive
    // calculations with no reset -- the teardown fix under real stress.
    cl::Event ev_d2h;
    OCL_CHECK(err, err = q.enqueueMigrateMemObjects(from_dev, CL_MIGRATE_MEM_OBJECT_HOST,
                                                    NULL, &ev_d2h));
    q.finish();

    // ---- measurements ------------------------------------------------------
    cl_ulong ks, ke;
    event_span(ev_k, ks, ke);
    const double kernel_ns = (double)(ke - ks);

    std::vector<cl::Event> one;
    one.push_back(ev_h2d);
    cl_ulong hs, he; event_span(one, hs, he);
    one.clear(); one.push_back(ev_d2h);
    cl_ulong ds, de; event_span(one, ds, de);

    // Index traffic is counted as real input bandwidth, because it is: 3 extra
    // PCs on every weight beat. That 8 -> 11 PC step is the interface cost of
    // sparsity and it belongs in the comparison, not in a footnote.
    const double w_bytes   = (double)n_weight_beats * W_PCS   * PC_BYTES;
    const double i_bytes   = (double)n_weight_beats * IND_PCS * PC_BYTES;
    const double a_bytes   = (double)n_act_beats    * A_PCS   * PC_BYTES;
    const double in_bytes  = w_bytes + i_bytes + a_bytes;
    const double outb      = (double)out_bytes * C_PCS;
    const double rows      = (double)(n_out_beats * LANES);

    // MACs actually PERFORMED: every beat, all 64 blocks do 2 each. This is the
    // honest sparse figure -- it counts non-zeros, not the zeros the format
    // skips. The nominal dense-equivalent work is rows * V, reported alongside
    // so the two architectures can be compared on the same logical problem.
    const double macs_done = (double)n_weight_beats * LANES * 2.0;
    const double macs_nominal = rows * (double)(n_act_beats * WIN_ELEMS);
    // One weight beat per cycle is the architectural roofline, so the ideal
    // time depends on the clock this bitstream was actually built for.
    const double ideal_ns  = (double)n_weight_beats * (1000.0 / clock_mhz);

    std::printf("\n--- measurements (OpenCL profiling events; excludes xclbin load) ---\n");
    std::printf("  clock assumed  : %10.0f MHz  (pass argv[3] if linked otherwise)\n",
                clock_mhz);
    std::printf("  H2D transfer   : %10.3f us   %8.3f GB/s   (%.0f bytes)\n",
                (he - hs) / 1e3, in_bytes / (double)(he - hs), in_bytes);
    std::printf("  kernel span    : %10.3f us   (earliest CU start -> latest CU end)\n",
                kernel_ns / 1e3);
    std::printf("  D2H transfer   : %10.3f us   %8.3f GB/s   (%.0f bytes)\n",
                (de - ds) / 1e3, outb / (double)(de - ds), outb);
    std::printf("\n  throughput     : %10.3f Mrow/s\n", rows / kernel_ns * 1e3);
    std::printf("  MAC rate       : %10.3f GMAC/s  (non-zeros actually computed)\n",
                macs_done / kernel_ns);
    std::printf("  effective rate : %10.3f GMAC/s  (dense-equivalent work: rows x V)\n",
                macs_nominal / kernel_ns);
    std::printf("  HBM bandwidth  : %10.3f GB/s   (%.0f bytes moved by the kernels)\n",
                (in_bytes + outb) / kernel_ns, in_bytes + outb);
    std::printf("    of which index: %10.3f GB/s   (%.1f %% of input bytes -- the\n"
                "                                    interface cost of sparsity)\n",
                i_bytes / kernel_ns, i_bytes / in_bytes * 100.0);
    std::printf("  beats/cycle    : %10.3f      (1.000 = one weight beat per clock)\n",
                ideal_ns / kernel_ns);
    std::printf("  efficiency     : %10.1f %%    vs the %.3f us ideal at %.0f MHz\n",
                ideal_ns / kernel_ns * 100.0, ideal_ns / 1e3, clock_mhz);
    std::printf("\n  NOTE: shared card -- run this several times and take the BEST kernel\n"
                "        span. XRT skips reconfiguration when the same xclbin is already\n"
                "        loaded, so repeat runs are cheap.\n");

    // ---- write output.txt in golden.txt order ------------------------------
    const std::string outpath = emu + "/output.txt";
    std::FILE* of = std::fopen(outpath.c_str(), "w");
    if (!of) { std::fprintf(stderr, "cannot write %s\n", outpath.c_str()); return 1; }
    for (size_t k = 0; k < n_out_beats; ++k) {
        for (int r = 0; r < LANES; ++r) {
            int pc = r / 16;
            int lane_in_pc = r % 16;
            const unsigned char* p = &c_img[pc].p[k * PC_BYTES + lane_in_pc * 2];
            unsigned v = (unsigned)p[0] | ((unsigned)p[1] << 8);
            std::fprintf(of, "%04X\n", v);
        }
    }
    std::fclose(of);
    std::printf("\nwrote %s  (%zu rows)\n", outpath.c_str(), n_out_beats * LANES);
    if (soak_iters)
        std::printf("(this is the LAST of %zu soak iterations -- comparing it clean means "
                    "the engine survived %zu consecutive calculations)\n",
                    soak_iters, soak_iters);
    std::printf("compare with:\n  cd %s && rm -f tlast.txt && python3 compare_gemv4_py36.py\n",
                emu.c_str());
    return 0;
}
