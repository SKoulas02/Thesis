// ---------------------------------------------------------------------------
// host_workload_xrt.cpp -- the workload host on the NATIVE XRT C++ API (xrt::device,
// xrt::kernel, xrt::bo, xrt::run), for the lowest host overhead per calculation.
//
// A NEW FILE (2026-09-29), derived from host_workload.cpp and host_workload_bcast.cpp (both
// untouched): the same command line, the same stimulus directories, the same lap walk, the
// same checks, the same output.txt and the same CALC / TENANT / PASS / SOAK lines, so
// run_workload.py parses it as it parses them. What is different, and why:
//
//   * NATIVE XRT, NOT OpenCL. Every buffer, kernel and run is created BEFORE any timing:
//     for every calculation of every tenant, one xrt::run per mover compute unit, with its
//     arguments already set. A calculation is then nothing but run.start() on each of its
//     movers -- no setArg, no command allocation, no event objects.
//   * ONE THREAD, POLLING. Completion is observed with run.state(), which reads the
//     command's state word straight from its exec buffer (XRT 2022.1
//     core/common/api/xrt_kernel.cpp: kernel_command::get_state -> get_state_raw). So one
//     thread serves all tenants:
//       independent (default)  every tenant runs its own list; the moment its current
//                              calculation completes, its next one starts (the multi-user
//                              case: different matrices, different vectors);
//       --lockstep             every tenant finishes calculation j before any starts j + 1
//                              (the MoE case: layer by layer).
//     No threads means none of the hardware-emulation race of 2026-09-29 (XRT's hw_emu shim
//     reads its buffer map unlocked while allocating into it: shim.cxx:2503/2594/3053), and
//     runs are built before timing, so nothing is allocated while commands are in flight.
//   * TIMES ARE HOST TIMES (std::chrono::steady_clock), not OpenCL profiling events, which
//     the native API does not have: start = just before a calculation's first mover is
//     started, active = just after its last INPUT mover is started, end = when polling
//     first sees its last mover complete. All relative to the start of the pass, in us.
//   * THREE BUILD TYPES:
//       normal           each tenant has its own activation movers mm2s_a<i>_t<k>
//       --shared-vector  the same CUs, but calculation j of every tenant reads the FIRST
//                        tenant's vector buffers (one copy in HBM[0..1]; shared builds)
//       --broadcast      no per-tenant activation movers: ONE pair mm2s_bcast_a0/a1 of
//                        krnl_mm2s_bcast<N> (N = the tenants, every engine of the build)
//                        streams calculation j's vector to all engines; implies --lockstep
//                        and --shared-vector; its runs count as inputs of every tenant.
//   * LIFETIMES: every XRT object lives in main's scope and is released in order (runs,
//     kernels, buffers, then the device) before main returns -- no global holds an XRT
//     object (a global list of events crashed the broadcast host at exit, 2026-09-29).
//   * BUFFERS go in the memory bank the compute unit's argument is connected to
//     (kernel.group_id(arg)), so no "first bound argument" rule is involved.
//
// USAGE
//   host_workload_xrt [--lockstep] [--shared-vector] [--broadcast] <xclbin> <clock_MHz>
//                     <timed_passes> <soak_seconds> <k:dir[,dir...]> ...
//     k        tenant index (the k in gemv_tk), or "s" for a single-engine bitstream
//     dir,...  that tenant's calculations, run in this order; each holds bin/*.bin and
//              receives output.txt
//
// OUTPUT (one line each, key=value), as host_workload.cpp:
//   CALC  pass tenant calc beats laps start_us active_us end_us   (host times, see above)
//   BCAST pass calc start_us end_us                               (--broadcast only)
//   TENANT pass tenant calcs busy_us active_us first_start_us last_end_us
//   PASS  pass makespan_us wall_us macs
//   SOAK_START_EPOCH / SOAK_END_EPOCH / SOAK passes seconds
//
// Build (one line; -DCORES/-DBLOCKS = the engine shape of ONE tenant; -lxrt_coreutil, not
// -lOpenCL; C++17, NOT c++1y: XRT 2.13's xrt_device.h uses std::any from C++17 on and needs
// the Boost headers (boost/any.hpp) below it -- xrt/detail/any.h):
//   g++ -Wall -O2 -std=c++17 -I$XILINX_XRT/include host_workload_xrt.cpp -L$XILINX_XRT/lib -lxrt_coreutil -lrt -pthread -DCORES=4 -DBLOCKS=4 -o host_workload_xrt
// ---------------------------------------------------------------------------

#include <xrt/xrt_bo.h>
#include <xrt/xrt_device.h>
#include <xrt/xrt_kernel.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <exception>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
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

// ---- 4096-byte aligned host memory, its size rounded up to whole pages: a user-pointer
// buffer must start on a page; the movers read only n bytes of it ----------------------
struct AlignedBuf {
    unsigned char* p;
    size_t n;                                   // bytes of data
    size_t cap;                                 // bytes allocated (n rounded up to ALIGN)
    AlignedBuf() : p(NULL), n(0), cap(0) {}
    void alloc(size_t bytes) {
        n = bytes;
        cap = ((bytes ? bytes : 1) + ALIGN - 1) / ALIGN * ALIGN;
        void* q = NULL;
        if (posix_memalign(&q, ALIGN, cap)) {
            std::fprintf(stderr, "posix_memalign failed for %zu bytes\n", cap);
            std::exit(1);
        }
        p = (unsigned char*)q;
        std::memset(p, 0, cap);
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

static inline unsigned sparsity_at(const AlignedBuf& ind_sp, size_t k) {
    return (unsigned)(ind_sp.p[k * PC_BYTES + SP_BYTE] & 0x3);
}

static double now_epoch() {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

typedef std::chrono::steady_clock Clock;
static inline double us_since(const Clock::time_point& origin) {
    return std::chrono::duration<double, std::micro>(Clock::now() - origin).count();
}

// ---------------------------------------------------------------------------
// One calculation: one stimulus directory = one activation vector, any number of laps.
// Its buffers and its pre-built runs (arguments fixed at setup).
// ---------------------------------------------------------------------------
struct Calc {
    std::string dir;
    std::vector<std::unique_ptr<AlignedBuf> > w_img, i_img, a_img, c_img;
    size_t n_weight_beats, n_act_beats, n_out_beats;
    std::vector<xrt::bo> b_w, b_i, b_a, b_c;    // b_a stays empty where the vector is shared
    // runs in LAUNCH ORDER: outputs, vector (own a-movers), indices, weights
    std::vector<xrt::run> r_c, r_a, r_i, r_w;
    std::vector<xrt::run> r_b;                  // --broadcast: the pair (tenant 0's calcs only)
    double t_start, t_active, t_end;            // us from the start of the pass

    Calc() : n_weight_beats(0), n_act_beats(0), n_out_beats(0),
             t_start(0), t_active(0), t_end(0) {
        for (int i = 0; i < W_PCS; ++i) w_img.emplace_back(new AlignedBuf());
        for (int i = 0; i < IND_PCS; ++i) i_img.emplace_back(new AlignedBuf());
        for (int i = 0; i < A_PCS; ++i) a_img.emplace_back(new AlignedBuf());
        for (int i = 0; i < C_PCS; ++i) c_img.emplace_back(new AlignedBuf());
    }
    size_t laps() const { return n_out_beats; }
    double macs() const { return (double)n_weight_beats * LANES * 2.0; }
};

struct Tenant {
    int id;                                     // -1 = single-engine bitstream (no suffix)
    std::vector<std::unique_ptr<Calc> > calcs;
    std::vector<xrt::kernel> k_w, k_i, k_a, k_c, k_b;
    bool drives_bcast;                          // --broadcast: tenant 0 holds the pair
    // scheduling state of the current pass
    size_t cur;                                 // calculation in flight (or next to start)
    bool in_flight;

    Tenant() : id(0), drives_bcast(false), cur(0), in_flight(false) {}
    std::string suffix() const { return id < 0 ? std::string() : "_t" + std::to_string(id); }
    std::string tag() const { return id < 0 ? std::string("s") : "t" + std::to_string(id); }
};

struct Options {
    bool lockstep, shared_vector, broadcast;
    Options() : lockstep(false), shared_vector(false), broadcast(false) {}
};

// Read one calculation's images and walk its laps (the multi-tenant hosts' logic).
static void load_calc(Calc& C, const std::string& who) {
    const std::string bindir = C.dir + "/bin";
    for (int i = 0; i < W_PCS; ++i)
        read_into(bindir + "/weights_pc" + std::to_string(i) + ".bin", *C.w_img[i]);
    for (int i = 0; i < IND_PCS; ++i)
        read_into(bindir + "/ind_pc" + std::to_string(i) + ".bin", *C.i_img[i]);
    for (int i = 0; i < A_PCS; ++i)
        read_into(bindir + "/act_pc" + std::to_string(i) + ".bin", *C.a_img[i]);

    C.n_weight_beats = C.w_img[0]->n / PC_BYTES;
    const size_t n_index_beats = C.i_img[0]->n / PC_BYTES;
    C.n_act_beats = C.a_img[0]->n / PC_BYTES;
    for (int i = 1; i < W_PCS; ++i)
        if (C.w_img[i]->n != C.w_img[0]->n) {
            std::fprintf(stderr, "%s %s: weights_pc%d.bin has another size than weights_pc0.bin\n",
                         who.c_str(), C.dir.c_str(), i);
            std::exit(1);
        }
    for (int i = 1; i < IND_PCS; ++i)
        if (C.i_img[i]->n != C.i_img[0]->n) {
            std::fprintf(stderr, "%s %s: ind_pc%d.bin has another size than ind_pc0.bin\n",
                         who.c_str(), C.dir.c_str(), i);
            std::exit(1);
        }
    if (C.a_img[1]->n != C.a_img[0]->n) {
        std::fprintf(stderr, "%s %s: act_pc1.bin has another size than act_pc0.bin\n",
                     who.c_str(), C.dir.c_str());
        std::exit(1);
    }
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
        const unsigned code = sparsity_at(*C.i_img[SP_PC], pos);
        const size_t nb = C.n_act_beats * (size_t)sp_freeze(code);
        if (pos + nb > C.n_weight_beats) {
            std::fprintf(stderr, "%s %s: stimulus truncated -- lap %zu at %s needs %zu beats, "
                         "%zu remain\n", who.c_str(), C.dir.c_str(), laps, SP_NAME[code], nb,
                         C.n_weight_beats - pos);
            std::exit(1);
        }
        for (size_t k = pos; k < pos + nb; ++k) {
            if (sparsity_at(*C.i_img[SP_PC], k) != code) {
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
    for (int i = 0; i < C_PCS; ++i) C.c_img[i]->alloc(C.n_out_beats * PC_BYTES);
    std::printf("%s calc %s: %zu weight beats, %zu laps (2:4 %zu, 2:8 %zu, 2:16 %zu, "
                "2:32 %zu), V=%zu\n", who.c_str(), C.dir.c_str(), C.n_weight_beats, laps,
                per_code[0], per_code[1], per_code[2], per_code[3],
                C.n_act_beats * WIN_ELEMS);
}

// The first U280 among the devices XRT sees (the server has several cards; in hw_emu the
// emulated device is index 0 and carries the platform name).
static xrt::device pick_u280() {
    for (unsigned idx = 0; idx < 16; ++idx) {
        xrt::device d;
        try {
            d = xrt::device(idx);
        } catch (const std::exception&) {
            break;                              // no device with this index: none left
        }
        const std::string name = d.get_info<xrt::info::device::name>();
        if (name.find("u280") != std::string::npos || name.find("U280") != std::string::npos) {
            std::printf("  device %u: %s\n", idx, name.c_str());
            return d;
        }
    }
    throw std::runtime_error("no U280 found -- refusing to run on the wrong card");
}

static xrt::kernel open_cu(const xrt::device& dev, const xrt::uuid& uuid,
                           const std::string& name) {
    try {
        return xrt::kernel(dev, uuid, name);
    } catch (const std::exception& e) {
        throw std::runtime_error("this xclbin has no compute unit " + name + " (" + e.what() +
                                 ")");
    }
}

// Kernels, every calculation's buffers (synced to the card) and its pre-built runs.
// first != NULL (--shared-vector or --broadcast, every tenant after the first): calculation
// j uses the first tenant's vector buffers instead of its own.
static void bind_tenant(Tenant& T, const xrt::device& dev, const xrt::uuid& uuid,
                        const Options& o, int nbcast, const Tenant* first) {
    const std::string s = T.suffix();
    for (int i = 0; i < W_PCS; ++i)
        T.k_w.push_back(open_cu(dev, uuid, "krnl_mm2s:{mm2s_w" + std::to_string(i) + s + "}"));
    for (int i = 0; i < IND_PCS; ++i)
        T.k_i.push_back(open_cu(dev, uuid, "krnl_mm2s:{mm2s_i" + std::to_string(i) + s + "}"));
    if (!o.broadcast)
        for (int i = 0; i < A_PCS; ++i)
            T.k_a.push_back(open_cu(dev, uuid, "krnl_mm2s:{mm2s_a" + std::to_string(i) + s + "}"));
    if (T.drives_bcast)
        for (int i = 0; i < A_PCS; ++i)
            T.k_b.push_back(open_cu(dev, uuid, "krnl_mm2s_bcast" + std::to_string(nbcast) +
                                    ":{mm2s_bcast_a" + std::to_string(i) + "}"));
    for (int i = 0; i < C_PCS; ++i)
        T.k_c.push_back(open_cu(dev, uuid, "krnl_s2mm:{s2mm_c" + std::to_string(i) + s + "}"));

    for (size_t j = 0; j < T.calcs.size(); ++j) {
        Calc& C = *T.calcs[j];
        const unsigned nw = (unsigned)C.n_weight_beats;
        const unsigned na = (unsigned)C.n_act_beats;
        const unsigned nc = (unsigned)C.n_out_beats;
        for (int i = 0; i < W_PCS; ++i) {
            C.b_w.push_back(xrt::bo(dev, C.w_img[i]->p, C.w_img[i]->cap, T.k_w[i].group_id(0)));
            C.b_w.back().sync(XCL_BO_SYNC_BO_TO_DEVICE);
        }
        for (int i = 0; i < IND_PCS; ++i) {
            C.b_i.push_back(xrt::bo(dev, C.i_img[i]->p, C.i_img[i]->cap, T.k_i[i].group_id(0)));
            C.b_i.back().sync(XCL_BO_SYNC_BO_TO_DEVICE);
        }
        if (!first) {                           // this tenant holds calculation j's vector
            for (int i = 0; i < A_PCS; ++i) {
                const int grp = o.broadcast ? T.k_b[i].group_id(0) : T.k_a[i].group_id(0);
                C.b_a.push_back(xrt::bo(dev, C.a_img[i]->p, C.a_img[i]->cap, grp));
                C.b_a.back().sync(XCL_BO_SYNC_BO_TO_DEVICE);
            }
        }
        for (int i = 0; i < C_PCS; ++i)
            C.b_c.push_back(xrt::bo(dev, C.c_img[i]->p, C.c_img[i]->cap, T.k_c[i].group_id(1)));

        // the runs, arguments fixed once. The mover arguments: krnl_mm2s (0 in, 1 stream,
        // 2 n_beats), krnl_s2mm (0 stream, 1 out, 2 n_beats), krnl_mm2s_bcast<N> (0 in,
        // 1..N streams, N+1 n_beats). Streams are never set by the host.
        const std::vector<xrt::bo>& vec = first ? first->calcs[j]->b_a : C.b_a;
        for (int i = 0; i < C_PCS; ++i) {
            xrt::run r(T.k_c[i]);
            r.set_arg(1, C.b_c[i]);
            r.set_arg(2, nc);
            C.r_c.push_back(r);
        }
        if (!o.broadcast)
            for (int i = 0; i < A_PCS; ++i) {
                xrt::run r(T.k_a[i]);
                r.set_arg(0, vec[i]);
                r.set_arg(2, na);
                C.r_a.push_back(r);
            }
        if (T.drives_bcast)
            for (int i = 0; i < A_PCS; ++i) {
                xrt::run r(T.k_b[i]);
                r.set_arg(0, vec[i]);
                r.set_arg(nbcast + 1, na);
                C.r_b.push_back(r);
            }
        for (int i = 0; i < IND_PCS; ++i) {
            xrt::run r(T.k_i[i]);
            r.set_arg(0, C.b_i[i]);
            r.set_arg(2, nw);
            C.r_i.push_back(r);
        }
        for (int i = 0; i < W_PCS; ++i) {
            xrt::run r(T.k_w[i]);
            r.set_arg(0, C.b_w[i]);
            r.set_arg(2, nw);
            C.r_w.push_back(r);
        }
    }
}

// ---------------------------------------------------------------------------
// Scheduling, one thread.
// ---------------------------------------------------------------------------
static void start_all(std::vector<xrt::run>& rs) {
    for (size_t i = 0; i < rs.size(); ++i) rs[i].start();
}

// The states a command can END in other than COMPLETED (xrt ert.h). NOT "anything above
// COMPLETED": ERT_CMD_STATE_SUBMITTED (7) is an in-flight state numbered above it.
static bool failed_state(ert_cmd_state st) {
    return st == ERT_CMD_STATE_ERROR || st == ERT_CMD_STATE_ABORT ||
           st == ERT_CMD_STATE_TIMEOUT || st == ERT_CMD_STATE_NORESPONSE ||
           st == ERT_CMD_STATE_SKERROR || st == ERT_CMD_STATE_SKCRASHED;
}

// Every run of the list complete? Throws on a run that ended in a failed state.
static bool all_done(std::vector<xrt::run>& rs, const std::string& what) {
    bool done = true;
    for (size_t i = 0; i < rs.size(); ++i) {
        const ert_cmd_state st = rs[i].state();
        if (st == ERT_CMD_STATE_COMPLETED) continue;
        if (failed_state(st))
            throw std::runtime_error(what + ": a mover ended in state " + std::to_string((int)st) +
                                     " (not COMPLETED)");
        done = false;                           // NEW, QUEUED, RUNNING, SUBMITTED: in flight
    }
    return done;
}

// A tenant's calculation, started: outputs first (every sink ready before a source pushes),
// then its own vector movers, then indices, then weights. (--broadcast starts the pair
// separately, see run_pass.)
static void start_calc(Tenant& T, Calc& C, const Clock::time_point& origin) {
    C.t_start = us_since(origin);
    start_all(C.r_c);
    start_all(C.r_a);
    start_all(C.r_i);
    start_all(C.r_w);
    C.t_active = us_since(origin);
    T.in_flight = true;
}

static bool calc_done(Tenant& T, Calc& C) {
    const std::string what = T.tag() + " " + C.dir;
    return all_done(C.r_c, what) && all_done(C.r_a, what) && all_done(C.r_i, what) &&
           all_done(C.r_w, what) && all_done(C.r_b, what);
}

// Poll until done() is true. Polling reads the command state in host memory; should a
// platform ever update it only on a wait, every 20 ms one still-running run (nudge()) is
// waited on for 1 ms -- that drives XRT's completion path, and costs nothing when polling
// works (a 1 ms wait every 20 ms, only in a calculation that lasts that long anyway).
template <typename Pred, typename Nudge>
static void poll_until(Pred done, Nudge nudge) {
    Clock::time_point last = Clock::now();
    while (!done()) {
        if (Clock::now() - last > std::chrono::milliseconds(20)) {
            xrt::run* r = nudge();
            if (r) r->wait(std::chrono::milliseconds(1));
            last = Clock::now();
        }
    }
}

// A run of this list that has not completed yet, or NULL.
static xrt::run* running_in(std::vector<xrt::run>& rs) {
    for (size_t i = 0; i < rs.size(); ++i)
        if (rs[i].state() != ERT_CMD_STATE_COMPLETED) return &rs[i];
    return NULL;
}

// One pass. keep_bcast: the broadcast pair's start/end per calculation (for the BCAST lines).
static void run_pass(std::vector<std::unique_ptr<Tenant> >& ts, const Options& o,
                     const Clock::time_point& origin,
                     std::vector<std::pair<double, double> >* bcast_times) {
    for (size_t t = 0; t < ts.size(); ++t) { ts[t]->cur = 0; ts[t]->in_flight = false; }

    if (o.lockstep) {
        // every tenant's calculation j, then wait for all of them, then j + 1
        const size_t ncalc = ts[0]->calcs.size();
        for (size_t j = 0; j < ncalc; ++j) {
            for (size_t t = 0; t < ts.size(); ++t)
                ts[t]->calcs[j]->t_start = us_since(origin);
            for (size_t t = 0; t < ts.size(); ++t) start_all(ts[t]->calcs[j]->r_c);
            double b0 = 0.0;
            if (o.broadcast) {
                b0 = us_since(origin);
                start_all(ts[0]->calcs[j]->r_b);
            }
            for (size_t t = 0; t < ts.size(); ++t) start_all(ts[t]->calcs[j]->r_a);
            for (size_t t = 0; t < ts.size(); ++t) {
                start_all(ts[t]->calcs[j]->r_i);
                start_all(ts[t]->calcs[j]->r_w);
                ts[t]->calcs[j]->t_active = us_since(origin);
            }
            // completion: each tenant's end when polling first sees all its movers done
            std::vector<bool> fin(ts.size(), false);
            bool bfin = !o.broadcast;
            double b1 = 0.0;
            size_t left = ts.size() + (bfin ? 0 : 1);
            poll_until([&]() {
                for (size_t t = 0; t < ts.size(); ++t) {
                    if (fin[t]) continue;
                    Calc& C = *ts[t]->calcs[j];
                    const std::string what = ts[t]->tag() + " " + C.dir;
                    if (all_done(C.r_c, what) && all_done(C.r_a, what) &&
                        all_done(C.r_i, what) && all_done(C.r_w, what)) {
                        C.t_end = us_since(origin);
                        fin[t] = true;
                        --left;
                    }
                }
                if (!bfin && all_done(ts[0]->calcs[j]->r_b, "broadcast pair")) {
                    b1 = us_since(origin);
                    bfin = true;
                    --left;
                }
                return left == 0;
            }, [&]() -> xrt::run* {
                for (size_t t = 0; t < ts.size(); ++t)
                    if (!fin[t]) return running_in(ts[t]->calcs[j]->r_c);
                return NULL;
            });
            if (o.broadcast && bcast_times) (*bcast_times)[j] = std::make_pair(b0, b1);
        }
        return;
    }

    // independent: every tenant walks its own list; one poll loop serves them all
    for (size_t t = 0; t < ts.size(); ++t)
        start_calc(*ts[t], *ts[t]->calcs[0], origin);
    size_t running = ts.size();
    poll_until([&]() {
        for (size_t t = 0; t < ts.size(); ++t) {
            Tenant& T = *ts[t];
            if (!T.in_flight) continue;
            Calc& C = *T.calcs[T.cur];
            if (!calc_done(T, C)) continue;
            C.t_end = us_since(origin);
            T.in_flight = false;
            if (++T.cur < T.calcs.size()) start_calc(T, *T.calcs[T.cur], origin);
            else --running;
        }
        return running == 0;
    }, [&]() -> xrt::run* {
        for (size_t t = 0; t < ts.size(); ++t)
            if (ts[t]->in_flight) return running_in(ts[t]->calcs[ts[t]->cur]->r_c);
        return NULL;
    });
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
            const unsigned char* p = &C.c_img[r / 16]->p[k * PC_BYTES + (r % 16) * 2];
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

// Every XRT object a tenant holds, released while the device still exists: runs first,
// then kernels, then buffers.
static void release_xrt(std::vector<std::unique_ptr<Tenant> >& ts) {
    for (size_t t = 0; t < ts.size(); ++t)
        for (size_t j = 0; j < ts[t]->calcs.size(); ++j) {
            Calc& C = *ts[t]->calcs[j];
            C.r_c.clear(); C.r_a.clear(); C.r_i.clear(); C.r_w.clear(); C.r_b.clear();
        }
    for (size_t t = 0; t < ts.size(); ++t) {
        Tenant& T = *ts[t];
        T.k_w.clear(); T.k_i.clear(); T.k_a.clear(); T.k_c.clear(); T.k_b.clear();
    }
    for (size_t t = 0; t < ts.size(); ++t)
        for (size_t j = 0; j < ts[t]->calcs.size(); ++j) {
            Calc& C = *ts[t]->calcs[j];
            C.b_w.clear(); C.b_i.clear(); C.b_a.clear(); C.b_c.clear();
        }
}

static int run_host(int argc, char** argv) {
    Options o;
    int ai = 1;                                  // leading options, then the positionals
    while (ai < argc && std::strncmp(argv[ai], "--", 2) == 0) {
        const std::string opt = argv[ai++];
        if (opt == "--lockstep") {
            o.lockstep = true;
        } else if (opt == "--shared-vector") {
            o.shared_vector = true;
        } else if (opt == "--broadcast") {
            o.broadcast = true;
        } else {
            std::fprintf(stderr, "unknown option %s (--lockstep, --shared-vector, --broadcast)\n",
                         opt.c_str());
            return 1;
        }
    }
    if (o.broadcast) o.lockstep = o.shared_vector = true;    // implied
    if (argc - ai < 5) {
        std::fprintf(stderr,
            "usage: %s [--lockstep] [--shared-vector] [--broadcast] <xclbin> <clock_MHz> "
            "<timed_passes> <soak_seconds> <k:dir[,dir...]> ...\n"
            "  k   tenant index (the k in gemv_tk), or s for a single-engine bitstream\n",
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
        if (k != "s" && k.find_first_not_of("0123456789") != std::string::npos) {
            std::fprintf(stderr, "bad tenant '%s' -- a number or s\n", k.c_str());
            return 1;
        }
        T->id = (k == "s") ? -1 : atoi(k.c_str());
        for (size_t j = 0; j < ts.size(); ++j) {
            if (ts[j]->id == T->id) {
                std::fprintf(stderr, "tenant %s given twice\n", k.c_str());
                return 1;
            }
        }
        const std::vector<std::string> dirs = split_commas(arg.substr(colon + 1));
        if (dirs.empty()) {
            std::fprintf(stderr, "tenant %s has no calculation\n", k.c_str());
            return 1;
        }
        for (size_t j = 0; j < dirs.size(); ++j) {
            std::unique_ptr<Calc> C(new Calc());
            C->dir = dirs[j];
            T->calcs.push_back(std::move(C));
        }
        ts.push_back(std::move(T));
    }
    for (size_t i = 0; i < ts.size(); ++i)
        if (ts[i]->id < 0 && ts.size() != 1) {
            std::fprintf(stderr, "tenant s (single engine) must be the only tenant\n");
            return 1;
        }
    if (ts[0]->id < 0 && (o.shared_vector || o.broadcast)) {
        std::fprintf(stderr, "--shared-vector/--broadcast need several tenants, not s\n");
        return 1;
    }
    // tenants in index order: tenant 0 first (it holds the shared vector / the pair)
    std::sort(ts.begin(), ts.end(), [](const std::unique_ptr<Tenant>& x,
                                       const std::unique_ptr<Tenant>& y) { return x->id < y->id; });
    if (o.broadcast) {
        // the pair feeds EVERY engine: all of them, each once, or one keeps a stale vector
        for (size_t i = 0; i < ts.size(); ++i)
            if (ts[i]->id != (int)i) {
                std::fprintf(stderr, "--broadcast: tenants must be exactly 0 .. N-1 (every engine "
                             "of the build, once each); tenant %zu is missing\n", i);
                return 1;
            }
        if (ts.size() < 2) {
            std::fprintf(stderr, "--broadcast: a broadcast build has at least 2 engines\n");
            return 1;
        }
        ts[0]->drives_bcast = true;
    }
    const int nbcast = (int)ts.size();

    // Old outputs go first, before anything can fail, so a compare can never read a
    // previous run's result.
    for (size_t i = 0; i < ts.size(); ++i)
        for (size_t j = 0; j < ts[i]->calcs.size(); ++j)
            std::remove((ts[i]->calcs[j]->dir + "/output.txt").c_str());
    if (!std::getenv("XILINX_XRT")) {
        std::fprintf(stderr, "XILINX_XRT is not set -- run: source /opt/xilinx/xrt/setup.sh\n");
        return 1;
    }

    std::printf("workload host (native XRT, one thread): %zu tenant(s) of %dx%d, %d timed "
                "pass(es), soak %.0f s%s%s%s\n", ts.size(), CORES, BLOCKS, timed_passes,
                soak_seconds, o.lockstep ? ", lockstep after every calculation" : ", independent",
                o.shared_vector ? ", ONE shared vector" : "",
                o.broadcast ? ", broadcast pair" : "");
    if (o.lockstep || o.shared_vector) {
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
    if (o.shared_vector) {
        // one buffer for everyone: every tenant's vector must be the same, byte for byte
        for (size_t i = 1; i < ts.size(); ++i)
            for (size_t j = 0; j < ts[i]->calcs.size(); ++j)
                for (int p = 0; p < A_PCS; ++p) {
                    const AlignedBuf& x = *ts[0]->calcs[j]->a_img[p];
                    const AlignedBuf& y = *ts[i]->calcs[j]->a_img[p];
                    if (x.n != y.n || std::memcmp(x.p, y.p, x.n) != 0) {
                        std::fprintf(stderr, "shared vector: %s act_pc%d.bin differs from %s -- "
                                     "the tenants do not share one vector\n",
                                     ts[i]->calcs[j]->dir.c_str(), p, ts[0]->calcs[j]->dir.c_str());
                        return 1;
                    }
                }
    }

    // ---- the card: device, xclbin, every kernel, buffer and run -------------------------
    xrt::device device = pick_u280();
    // Declared AFTER the device, so it is destroyed BEFORE it on every way out of this
    // function -- the normal return and an exception alike: every run, kernel and buffer the
    // tenants hold is released while the device still exists.
    struct Releaser {
        std::vector<std::unique_ptr<Tenant> >& ts;
        ~Releaser() { release_xrt(ts); }
    } releaser{ts};
    const xrt::uuid uuid = device.load_xclbin(xclbin_path);
    std::printf("xclbin loaded: %s\n", xclbin_path.c_str());
    for (size_t i = 0; i < ts.size(); ++i)
        bind_tenant(*ts[i], device, uuid, o, nbcast,
                    (o.shared_vector && i > 0) ? ts[0].get() : NULL);
    std::printf("all data on the card, every run built; %.0f MACs per pass (padding included)\n",
                pass_macs);
    std::fflush(stdout);

    // ---- timed passes ---------------------------------------------------------------------
    std::vector<std::pair<double, double> > btimes(ts[0]->calcs.size());
    for (int p = 0; p < timed_passes; ++p) {
        const double w0 = now_epoch();
        const Clock::time_point origin = Clock::now();
        run_pass(ts, o, origin, o.broadcast ? &btimes : NULL);
        const double wall_us = (now_epoch() - w0) * 1e6;
        double last = 0.0;
        for (size_t i = 0; i < ts.size(); ++i)
            for (size_t j = 0; j < ts[i]->calcs.size(); ++j)
                last = std::max(last, ts[i]->calcs[j]->t_end);
        if (o.broadcast)
            for (size_t j = 0; j < btimes.size(); ++j)
                std::printf("BCAST pass=%d calc=%zu start_us=%.3f end_us=%.3f\n", p, j,
                            btimes[j].first, btimes[j].second);
        for (size_t i = 0; i < ts.size(); ++i) {
            Tenant& T = *ts[i];
            double busy = 0.0, active = 0.0, first = 1e300, end = 0.0;
            for (size_t j = 0; j < T.calcs.size(); ++j) {
                const Calc& C = *T.calcs[j];
                std::printf("CALC pass=%d tenant=%s calc=%zu beats=%zu laps=%zu start_us=%.3f "
                            "active_us=%.3f end_us=%.3f\n", p, T.tag().c_str(), j,
                            C.n_weight_beats, C.laps(), C.t_start, C.t_active, C.t_end);
                busy += C.t_end - C.t_start;
                active += C.t_end - C.t_active;
                first = std::min(first, C.t_start);
                end = std::max(end, C.t_end);
            }
            std::printf("TENANT pass=%d tenant=%s calcs=%zu busy_us=%.3f active_us=%.3f "
                        "first_start_us=%.3f last_end_us=%.3f\n", p, T.tag().c_str(),
                        T.calcs.size(), busy, active, first, end);
        }
        std::printf("PASS pass=%d makespan_us=%.3f wall_us=%.3f macs=%.0f\n", p, last, wall_us,
                    pass_macs);
        std::fflush(stdout);
    }

    // ---- outputs of the last timed pass (the correctness check reads these) ---------------
    for (size_t i = 0; i < ts.size(); ++i)
        for (size_t j = 0; j < ts[i]->calcs.size(); ++j) {
            Calc& C = *ts[i]->calcs[j];
            for (size_t b = 0; b < C.b_c.size(); ++b) C.b_c[b].sync(XCL_BO_SYNC_BO_FROM_DEVICE);
            write_output(C);
        }
    std::printf("outputs written (last timed pass)\n");
    std::fflush(stdout);

    // ---- power soak: whole passes, back to back ---------------------------------------------
    if (soak_seconds > 0.0) {
        const double t0 = now_epoch();
        std::printf("SOAK_START_EPOCH %.3f\n", t0);
        std::fflush(stdout);
        size_t passes = 0;
        while (now_epoch() - t0 < soak_seconds) {
            run_pass(ts, o, Clock::now(), NULL);
            ++passes;
        }
        const double t1 = now_epoch();
        std::printf("SOAK_END_EPOCH %.3f\n", t1);
        std::printf("SOAK passes=%zu seconds=%.3f\n", passes, t1 - t0);
        std::fflush(stdout);
    }

    return 0;                                    // releaser, then the device, go out of scope
}

int main(int argc, char** argv) {
    try {
        return run_host(argc, argv);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "XRT ERROR: %s\n", e.what());
        return 1;
    }
}
