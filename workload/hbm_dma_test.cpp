// ---------------------------------------------------------------------------
// hbm_dma_test.cpp -- DIAGNOSTIC: is the host <-> HBM DMA path of a bitstream clean?
//
// A NEW FILE (2026-10-01), written to troubleshoot 2x8x8_bcast: its gates fail with the SAME
// output bits forced to 0 (bits 16, 17 and 134 of every even 32-byte beat, in every output
// channel of both engines), under either scheduler and on any data. That corruption is either
// on the KERNEL's write path (output FIFO -> die crossing -> s2mm mover -> HBM) or on the DMA
// READ path (HBM -> PCIe -> host) of this bitstream's HBM subsystem. This program involves no
// kernel at all: it opens every mover the workload host opens (the same compute-unit names),
// takes the HBM bank behind each one (kernel.group_id, as the host does), and for every bank
// writes known patterns from the host, reads them back, and reports every wrong bit by its
// position within a 64-byte block (which 32-byte beat, which bit) and its direction.
//   clean everywhere   -> the DMA path is fine: the fault is on the kernel's write path
//   the same bits bad  -> the DMA read (or write) path of this bitstream is the fault
//
// USAGE (in the build folder; nothing else may be using the card):
//   g++ -Wall -O2 -std=c++17 -I$XILINX_XRT/include hbm_dma_test.cpp -L$XILINX_XRT/lib -lxrt_coreutil -lrt -pthread -DCORES=8 -DBLOCKS=8 -o hbm_dma_test
//   ./hbm_dma_test <xclbin> <engines> [--broadcast] [--mb N] [--offset B]
//     engines 1 = a single-engine bitstream (no _t<k> suffix); --broadcast = a *_bcast build
//     --offset B: write the whole buffer, but read it back starting B bytes in (one DMA of n - B
//     bytes). Beats are always numbered from the BUFFER start: if a fault seen on even beats moves to
//     the odd ones with --offset 32, it follows the transfer (a two-read workaround exists); if it
//     stays on the even beats, it follows the address.
// Exit 0 = every bank clean, 2 = bit errors found, 1 = could not run.
// ---------------------------------------------------------------------------

#include <xrt/xrt_bo.h>
#include <xrt/xrt_device.h>
#include <xrt/xrt_kernel.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

#ifndef CORES
#define CORES 4
#endif
#ifndef BLOCKS
#define BLOCKS 4
#endif
static const int LANES = CORES * BLOCKS;
static const int W_PCS = (32 * LANES + 255) / 256;
static const int IND_PCS = (10 * LANES + 2 + 255) / 256;
static const int A_PCS = 2;
static const int C_PCS = (16 * LANES + 255) / 256;
static const size_t ALIGN = 4096;

static xrt::device pick_u280() {
    for (unsigned idx = 0; idx < 16; ++idx) {
        xrt::device d;
        try {
            d = xrt::device(idx);
        } catch (const std::exception&) {
            break;
        }
        const std::string name = d.get_info<xrt::info::device::name>();
        if (name.find("u280") != std::string::npos || name.find("U280") != std::string::npos) {
            std::printf("device %u: %s\n", idx, name.c_str());
            return d;
        }
    }
    throw std::runtime_error("no U280 found");
}

struct Port {
    std::string cu;                             // e.g. krnl_s2mm:{s2mm_c0_t1}
    int arg;                                    // the buffer argument
    std::string role;                           // weights / indices / vector / output
};

static unsigned long long xorshift(unsigned long long& s) {
    s ^= s << 13;
    s ^= s >> 7;
    s ^= s << 17;
    return s;
}

static void fill(unsigned char* p, size_t n, int pattern, unsigned long long seed) {
    if (pattern == 0) { std::memset(p, 0xFF, n); return; }
    if (pattern == 1) { std::memset(p, 0x00, n); return; }
    if (pattern == 2) {
        for (size_t i = 0; i < n; ++i) p[i] = (i & 1) ? 0x55 : 0xAA;
        return;
    }
    unsigned long long s = seed | 1;
    for (size_t i = 0; i < n; i += 8) {
        const unsigned long long v = xorshift(s);
        std::memcpy(p + i, &v, (n - i) < 8 ? (n - i) : 8);
    }
}


int main(int argc, char** argv) {
    std::vector<std::string> args;
    bool bcast = false;
    size_t mb = 4, off = 0;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--broadcast") bcast = true;
        else if (a == "--mb" && i + 1 < argc) mb = (size_t)std::atoi(argv[++i]);
        else if (a == "--offset" && i + 1 < argc) off = (size_t)std::atoi(argv[++i]);
        else args.push_back(a);
    }
    if (off % 32 || off >= 1024 * 1024) {
        std::fprintf(stderr, "--offset must be a multiple of 32 bytes, below 1 MB\n");
        return 1;
    }
    if (args.size() != 2 || mb < 1) {
        std::fprintf(stderr, "usage: %s <xclbin> <engines> [--broadcast] [--mb N] [--offset B]\n", argv[0]);
        return 1;
    }
    const std::string xclbin = args[0];
    const int engines = std::atoi(args[1].c_str());
    if (engines < 1 || (bcast && engines < 2)) {
        std::fprintf(stderr, "engines must be >= 1 (>= 2 with --broadcast)\n");
        return 1;
    }
    try {
        xrt::device dev = pick_u280();
        const xrt::uuid uuid = dev.load_xclbin(xclbin);
        std::printf("xclbin %s, %d engine(s) of %dx%d%s; per engine %d weight + %d index + %d "
                    "output channels\n", xclbin.c_str(), engines, CORES, BLOCKS,
                    bcast ? ", broadcast vector" : "", W_PCS, IND_PCS, C_PCS);
        std::vector<Port> ports;
        for (int k = 0; k < engines; ++k) {
            const std::string s = engines == 1 ? "" : "_t" + std::to_string(k);
            for (int i = 0; i < W_PCS; ++i)
                ports.push_back({"krnl_mm2s:{mm2s_w" + std::to_string(i) + s + "}", 0, "weights"});
            for (int i = 0; i < IND_PCS; ++i)
                ports.push_back({"krnl_mm2s:{mm2s_i" + std::to_string(i) + s + "}", 0, "indices"});
            if (!bcast)
                for (int i = 0; i < A_PCS; ++i)
                    ports.push_back({"krnl_mm2s:{mm2s_a" + std::to_string(i) + s + "}", 0,
                                     "vector"});
            for (int i = 0; i < C_PCS; ++i)
                ports.push_back({"krnl_s2mm:{s2mm_c" + std::to_string(i) + s + "}", 1, "OUTPUT"});
        }
        if (bcast)
            for (int i = 0; i < A_PCS; ++i)
                ports.push_back({"krnl_mm2s_bcast" + std::to_string(engines) + ":{mm2s_bcast_a" +
                                 std::to_string(i) + "}", 0, "vector"});
        // bank of every port
        std::map<int, std::vector<std::string> > banks;      // group id -> the ports on it
        std::map<int, bool> has_output;
        for (size_t i = 0; i < ports.size(); ++i) {
            xrt::kernel k(dev, uuid, ports[i].cu);
            const int g = k.group_id(ports[i].arg);
            banks[g].push_back(ports[i].role + " " + ports[i].cu);
            if (ports[i].role == "OUTPUT") has_output[g] = true;
        }
        const std::string rb = off ? "starts " + std::to_string(off) + " bytes into each buffer "
                                     "(beats still numbered from the buffer start)" : "whole buffers";
        std::printf("%zu movers on %zu memory banks; read-back %s\n\n", ports.size(),
                    banks.size(), rb.c_str());

        const size_t n = mb * 1024 * 1024;
        unsigned char* host = NULL;
        unsigned char* want = NULL;
        if (posix_memalign((void**)&host, ALIGN, n) || posix_memalign((void**)&want, ALIGN, n)) {
            std::fprintf(stderr, "out of memory\n");
            return 1;
        }
        int bad_banks = 0;
        for (std::map<int, std::vector<std::string> >::const_iterator it = banks.begin();
             it != banks.end(); ++it) {
            const int g = it->first;
            // errors[(beat parity, bit in the 32-byte beat, direction)] = count
            std::map<std::string, size_t> errors;
            size_t total = 0;
            {
                xrt::bo bo(dev, host, n, g);
                for (int pat = 0; pat < 4; ++pat) {
                    fill(want, n, pat, 0x9E3779B97F4A7C15ULL ^ (unsigned long long)(g * 7919 + 1));
                    std::memcpy(host, want, n);
                    bo.sync(XCL_BO_SYNC_BO_TO_DEVICE);
                    for (size_t i = 0; i < n; ++i) host[i] = (unsigned char)~want[i];   // stale = wrong
                    if (off)
                        bo.sync(XCL_BO_SYNC_BO_FROM_DEVICE, n - off, off);
                    else
                        bo.sync(XCL_BO_SYNC_BO_FROM_DEVICE);
                    for (size_t i = off; i < n; ++i) {
                        const unsigned diff = (unsigned)(host[i] ^ want[i]);
                        if (!diff) continue;
                        for (int b = 0; b < 8; ++b) {
                            if (!((diff >> b) & 1)) continue;
                            const size_t beat = i / 32;
                            const int bit = (int)((i % 32) * 8 + b);
                            const bool was1 = (want[i] >> b) & 1;
                            char key[96];
                            std::snprintf(key, sizeof key, "%s beat, bit %3d (lane %2d bit %2d), %s",
                                          (beat % 2) ? "odd " : "even", bit, bit / 16, bit % 16,
                                          was1 ? "1->0" : "0->1");
                            ++errors[key];
                            ++total;
                        }
                    }
                }
            }
            std::printf("bank %2d (%s): %s\n", g, has_output.count(g) ? "has an OUTPUT" : "inputs",
                        total ? "BIT ERRORS" : "clean (4 patterns, both directions)");
            for (size_t i = 0; i < it->second.size(); ++i)
                std::printf("    %s\n", it->second[i].c_str());
            if (total) {
                ++bad_banks;
                std::printf("    %zu wrong bits over %zu MB x 4 patterns:\n", total, mb);
                size_t shown = 0;
                for (std::map<std::string, size_t>::const_iterator e = errors.begin();
                     e != errors.end() && shown < 12; ++e, ++shown)
                    std::printf("      %-50s %zu\n", e->first.c_str(), e->second);
                if (errors.size() > 12) std::printf("      ... %zu more positions\n", errors.size() - 12);
            }
        }
        free(host);
        free(want);
        std::printf("\n%s\n", bad_banks ? "=== DMA PATH HAS BIT ERRORS on the banks above"
                                         : "=== DMA PATH CLEAN on every bank this bitstream uses");
        return bad_banks ? 2 : 0;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "XRT ERROR: %s\n", e.what());
        return 1;
    }
}
