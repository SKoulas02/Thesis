// ---------------------------------------------------------------------------
// host.cpp -- OpenCL host for the DENSE GEMV kernel system.
//
//   10 x krnl_mm2s  ->  krnl_gemv_dense (free-running)  ->  4 x krnl_s2mm
//
// OpenCL rather than the XRT native API because every 2021.1 RTL-kernel example
// uses it, and it is the better-supported path for xrt.ini profiling on this
// release (INTEGRATION_PLAN.md 0.2e). Self-contained -- uses the cl2.hpp that
// ships with XRT, so there is no xcl2 helper to vendor.
//
// The compute kernel is FREE-RUNNING (ap_ctrl_none): it has no control
// interface, is never referenced here, and starts when the xclbin is
// programmed. Its stream ports take no setArg.
//
// Sizes are DERIVED from the image files rather than hardcoded, so the same
// binary runs the small emulation case and the full V=1024 case:
//     n_weight_beats = weights_pc0.bin / 32
//     n_act_beats    = act_pc0.bin     / 32          (= V/32 = windows)
//     beats per lap  = n_act_beats * 16              (dense: V/2)
//     n_out_beats    = n_weight_beats / beats_per_lap  = laps
//
// Writes output.txt in golden.txt's format, so compare_dense_py36.py runs
// unchanged -- the same golden model from RTL simulation through to hardware.
//
// ---- MEASUREMENT NOTES (S22) ----------------------------------------------
// The U280 is SHARED. Other users load their own xclbins, so load_xclbin here
// usually triggers a full FPGA reconfiguration -- seconds, and however long
// depends on what the previous user left loaded. Therefore:
//
//   * timing comes from OpenCL PROFILING EVENTS on the kernels, never from
//     wall clock around the program;
//   * host->device and device->host migrations are timed separately, so PCIe
//     transfer is never confused with compute;
//   * buffers are 4096-byte aligned (posix_memalign), so XRT DMAs straight
//     from them instead of staging through an extra memcpy. Unaligned buffers
//     produce "unaligned host pointer" warnings and put real host-side copy
//     time inside the transfer numbers.
//
// Back-to-back runs of this program are cheap: XRT skips reconfiguration when
// the requested xclbin UUID is already loaded. Run it several times and take
// the best kernel span -- on a shared machine a single sample is noisy.
//
// BUILD (server):
//   g++ -Wall -O2 -std=c++14 -I$XILINX_XRT/include host.cpp \
//       -L$XILINX_XRT/lib -lOpenCL -lrt -lstdc++ -pthread -o host
//
// RUN:
//   hw_emu : XCL_EMULATION_MODE=hw_emu ./host gemv_dense.hw_emu.xclbin <emu_dir>
//   hw     : ./host gemv_dense.xclbin <emu_dir> [clock_MHz] [soak_seconds]
//
// CLOCK_MHZ (optional, default 300) is the frequency the .xclbin was LINKED at.
// Dense closes at 300, so the default is right for it; the argument exists so
// this host and host_sparse take the same command line and one measurement
// script can drive both.
//
// SOAK_SECONDS (optional, default 0 = off) drives POWER MEASUREMENT. One
// calculation lasts a few ms while `xbutil examine` takes about a second, so a
// single run cannot be sampled. With soak_seconds > 0 the host re-enqueues all
// 14 CUs back-to-back for that long with the buffers ALREADY RESIDENT -- no H2D
// between iterations -- giving a sustained compute-only window, and prints its
// bounds as epoch seconds so a sampler can clip to exactly the load.
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
static const int A_PCS = 2;
static const int C_PCS = 4;
static const int LANES = 64;      // 8 cores x 8 blocks
static const int BEATS_PER_WINDOW = 16;   // dense: 32 elements / 2 per cycle
static const size_t ALIGN = 4096;         // XRT DMA alignment

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
// std::vector's allocator gives no alignment guarantee, and XRT then stages
// every transfer through an extra memcpy. That copy is real host time and it
// would sit inside the migration numbers below.
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

// Span in nanoseconds from the earliest START to the latest END across events.
// With 14 concurrent CUs that span IS the compute phase; summing per-CU
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
            "  clock_MHz    : what the xclbin was LINKED at (default 300)\n"
            "  soak_seconds : 0 = off (default). >0 re-runs the kernels "
            "back-to-back for that long,\n"
            "                 buffers resident, for POWER measurement.\n",
            argv[0]);
        return 1;
    }
    const std::string xclbin_path = argv[1];
    const std::string emu = argv[2];
    const std::string bindir = emu + "/bin";
    const double clock_mhz = (argc > 3) ? atof(argv[3]) : 300.0;
    if (clock_mhz <= 0.0) {
        std::fprintf(stderr, "clock_MHz must be positive\n");
        return 1;
    }
    // Wall-clock DURATION, not an iteration count, so every configuration gets
    // an identical measurement window regardless of how long one run takes.
    const double soak_seconds = (argc > 4) ? atof(argv[4]) : 0.0;

    cl_int err = CL_SUCCESS;

    // ---- load the per-PC images and derive every size from them ------------
    std::vector<AlignedBuf> w_img(W_PCS), a_img(A_PCS), c_img(C_PCS);
    for (int i = 0; i < W_PCS; ++i)
        read_into(bindir + "/weights_pc" + std::to_string(i) + ".bin", w_img[i]);
    for (int i = 0; i < A_PCS; ++i)
        read_into(bindir + "/act_pc" + std::to_string(i) + ".bin", a_img[i]);

    const size_t n_weight_beats = w_img[0].n / PC_BYTES;
    const size_t n_act_beats    = a_img[0].n / PC_BYTES;
    const size_t beats_per_lap  = n_act_beats * BEATS_PER_WINDOW;
    if (beats_per_lap == 0 || n_weight_beats % beats_per_lap) {
        std::fprintf(stderr, "inconsistent stimulus: %zu weight beats vs %zu beats/lap\n",
                     n_weight_beats, beats_per_lap);
        return 1;
    }
    const size_t n_out_beats = n_weight_beats / beats_per_lap;   // = laps
    const size_t out_bytes   = n_out_beats * PC_BYTES;
    for (int i = 0; i < C_PCS; ++i) c_img[i].alloc(out_bytes);

    std::printf("stimulus: %zu weight beats, %zu activation beats (V=%zu)\n",
                n_weight_beats, n_act_beats, n_act_beats * 32);
    std::printf("          %zu beats/lap -> %zu lap(s) -> %zu output beats = %zu rows\n",
                beats_per_lap, n_out_beats, n_out_beats, n_out_beats * LANES);

    // ---- device, context, queue --------------------------------------------
    cl::Device device = pick_u280();
    cl::Context context(device, NULL, NULL, NULL, &err);

    // OUT-OF-ORDER is load-bearing: on an in-order queue the 14 CUs serialise
    // and a producer waits on a consumer that cannot start -- deadlock.
    // PROFILING gives us the event timestamps used below.
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
    // the runtime pick any of the ten, each bound to a different HBM channel --
    // wrong data, and no error anywhere.
    std::vector<cl::Kernel> k_w(W_PCS), k_a(A_PCS), k_c(C_PCS);
    for (int i = 0; i < W_PCS; ++i)
        k_w[i] = cl::Kernel(program, ("krnl_mm2s:{mm2s_w" + std::to_string(i) + "}").c_str(), &err);
    for (int i = 0; i < A_PCS; ++i)
        k_a[i] = cl::Kernel(program, ("krnl_mm2s:{mm2s_a" + std::to_string(i) + "}").c_str(), &err);
    for (int i = 0; i < C_PCS; ++i)
        k_c[i] = cl::Kernel(program, ("krnl_s2mm:{s2mm_c" + std::to_string(i) + "}").c_str(), &err);
    // krnl_gemv_dense: free-running, no handle, never started by the host.

    // ---- buffers -----------------------------------------------------------
    std::vector<cl::Buffer> b_w(W_PCS), b_a(A_PCS), b_c(C_PCS);
    std::vector<cl::Memory> to_dev, from_dev;

    for (int i = 0; i < W_PCS; ++i) {
        b_w[i] = cl::Buffer(context, CL_MEM_USE_HOST_PTR | CL_MEM_READ_ONLY,
                            w_img[i].n, w_img[i].p, &err);
        k_w[i].setArg(0, b_w[i]);
        k_w[i].setArg(2, (unsigned)n_weight_beats);   // arg 1 is the STREAM -- never set
        to_dev.push_back(b_w[i]);
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
    // Order is irrelevant on an out-of-order queue: all 14 CUs run together.
    std::vector<cl::Event> ev_k;
    ev_k.reserve(W_PCS + A_PCS + C_PCS);
    for (int i = 0; i < C_PCS; ++i) { cl::Event e; OCL_CHECK(err, err = q.enqueueTask(k_c[i], NULL, &e)); ev_k.push_back(e); }
    for (int i = 0; i < A_PCS; ++i) { cl::Event e; OCL_CHECK(err, err = q.enqueueTask(k_a[i], NULL, &e)); ev_k.push_back(e); }
    for (int i = 0; i < W_PCS; ++i) { cl::Event e; OCL_CHECK(err, err = q.enqueueTask(k_w[i], NULL, &e)); ev_k.push_back(e); }

    std::printf("launched %zu CUs, waiting...\n", ev_k.size());
    q.finish();
    std::printf("all CUs done\n");

    // ---- POWER SOAK (optional) ---------------------------------------------
    // Buffers stay resident: this loop enqueues ONLY the kernels, never a
    // migration, so the card is doing compute and HBM traffic and nothing else.
    size_t soak_iters = 0;
    double soak_t0 = 0.0, soak_t1 = 0.0;
    if (soak_seconds > 0.0) {
        std::printf("\n--- power soak: %.1f s, buffers resident, compute only ---\n",
                    soak_seconds);
        struct timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        soak_t0 = ts.tv_sec + ts.tv_nsec / 1e9;
        std::printf("SOAK_START_EPOCH %.3f\n", soak_t0);
        std::fflush(stdout);

        double now = soak_t0;
        while (now - soak_t0 < soak_seconds) {
            for (int i = 0; i < C_PCS; ++i) OCL_CHECK(err, err = q.enqueueTask(k_c[i]));
            for (int i = 0; i < A_PCS; ++i) OCL_CHECK(err, err = q.enqueueTask(k_a[i]));
            for (int i = 0; i < W_PCS; ++i) OCL_CHECK(err, err = q.enqueueTask(k_w[i]));
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
    // calculations with no reset.
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

    const double in_bytes  = (double)(n_weight_beats * W_PCS + n_act_beats * A_PCS) * PC_BYTES;
    const double outb      = (double)out_bytes * C_PCS;
    const double rows      = (double)(n_out_beats * LANES);
    const double macs      = rows * (double)(n_act_beats * 32);   // rows x V
    // One weight beat per cycle is the architectural roofline, so the ideal time
    // depends on the clock this bitstream was actually built for.
    const double ideal_ns  = (double)n_weight_beats * (1000.0 / clock_mhz);

    std::printf("\n--- measurements (OpenCL profiling events; excludes xclbin load) ---\n");
    std::printf("  H2D transfer   : %10.3f us   %8.3f GB/s   (%.0f bytes)\n",
                (he - hs) / 1e3, in_bytes / (double)(he - hs), in_bytes);
    std::printf("  kernel span    : %10.3f us   (earliest CU start -> latest CU end)\n",
                kernel_ns / 1e3);
    std::printf("  D2H transfer   : %10.3f us   %8.3f GB/s   (%.0f bytes)\n",
                (de - ds) / 1e3, outb / (double)(de - ds), outb);
    std::printf("\n  throughput     : %10.3f Mrow/s\n", rows / kernel_ns * 1e3);
    std::printf("  MAC rate       : %10.3f GMAC/s\n", macs / kernel_ns);
    std::printf("  HBM bandwidth  : %10.3f GB/s   (%.0f bytes moved by the kernels)\n",
                (in_bytes + outb) / kernel_ns, in_bytes + outb);
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
    std::printf("compare with:\n  cd %s && rm -f tlast.txt && python3 compare_dense_py36.py\n",
                emu.c_str());
    return 0;
}
