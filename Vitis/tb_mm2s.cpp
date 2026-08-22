// ---------------------------------------------------------------------------
// tb_mm2s.cpp -- csim/cosim testbench for krnl_mm2s (INTEGRATION_STEPS.md S12).
//
// THE POINT (INTEGRATION_PLAN.md 2.3): the mover's whole job is to reproduce,
// out of HBM, the exact beats the RTL was verified against. So this testbench
// does not invent stimulus -- it runs the kernel over the per-PC binary images
// produced by hex_to_bin.py, reassembles the bus words, and compares them
// against the ORIGINAL weights.hex / activations.hex line for line.
//
// If that passes, the entire compute-side verification chain built during the
// RTL phase carries over to hardware unchanged.
//
// Also checks the AXIS sidebands, which no data compare can see:
//   * TLAST on the final beat of each CU's loop and no other
//   * TKEEP all ones on every beat (UG1393 forbids anything else here)
//
// Paths are the server layout; override at compile time with
//   -DEMU_DIR=\"/some/other/Emulation\"
// ---------------------------------------------------------------------------

#include <ap_int.h>
#include <ap_axi_sdata.h>
#include <hls_stream.h>

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#ifndef EMU_DIR
#define EMU_DIR "/home/skoulas/GEMV_Dense/GEMV_Dense_Source/Emulation"
#endif

#define DWIDTH 256
typedef ap_axiu<DWIDTH, 0, 0, 0> pkt;

extern "C" void krnl_mm2s(const ap_uint<DWIDTH>* in, hls::stream<pkt>& out,
                          unsigned int n_beats);

// ---- helpers ---------------------------------------------------------------

// Read a per-PC binary image into 256-bit words (32 bytes each, LSB first).
static bool load_pc(const std::string& path, std::vector<ap_uint<DWIDTH> >& out) {
    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) {
        std::printf("  ERROR: cannot open %s\n", path.c_str());
        return false;
    }
    unsigned char buf[DWIDTH / 8];
    out.clear();
    while (std::fread(buf, 1, sizeof(buf), f) == sizeof(buf)) {
        ap_uint<DWIDTH> v = 0;
        for (int i = 0; i < (int)sizeof(buf); ++i)
            v.range(8 * i + 7, 8 * i) = buf[i];
        out.push_back(v);
    }
    std::fclose(f);
    return true;
}

// 256-bit word -> 64 uppercase hex chars, MSB first.
static std::string pc_hex(const ap_uint<DWIDTH>& v) {
    static const char* D = "0123456789ABCDEF";
    std::string s;
    for (int n = DWIDTH / 4 - 1; n >= 0; --n)
        s += D[(unsigned)v.range(4 * n + 3, 4 * n)];
    return s;
}

// Read a .hex file: one bus beat per line, uppercase, fixed width.
static bool load_hex(const std::string& path, std::vector<std::string>& out) {
    FILE* f = std::fopen(path.c_str(), "r");
    if (!f) {
        std::printf("  ERROR: cannot open %s\n", path.c_str());
        return false;
    }
    char line[8192];
    out.clear();
    while (std::fgets(line, sizeof(line), f)) {
        std::string s(line);
        while (!s.empty() && (s[s.size() - 1] == '\n' || s[s.size() - 1] == '\r'))
            s.erase(s.size() - 1);
        if (!s.empty()) out.push_back(s);
    }
    std::fclose(f);
    return true;
}

// Run krnl_mm2s over `npcs` PC images and rebuild the wide bus beats.
// PC i occupies bits [256i+255 : 256i], so the hex line is PC(n-1) .. PC0.
static int check_stream(const char* label, const char* prefix, int npcs,
                        const char* hexname) {
    std::printf("%s:\n", label);

    std::vector<std::vector<ap_uint<DWIDTH> > > img(npcs);
    for (int i = 0; i < npcs; ++i) {
        char p[1024];
        std::snprintf(p, sizeof(p), "%s/bin/%s%d.bin", EMU_DIR, prefix, i);
        if (!load_pc(p, img[i])) return 1;
    }

    const unsigned nbeats = (unsigned)img[0].size();
    for (int i = 1; i < npcs; ++i) {
        if (img[i].size() != nbeats) {
            std::printf("  ERROR: %s%d has %u beats, %s0 has %u\n",
                        prefix, i, (unsigned)img[i].size(), prefix, nbeats);
            return 1;
        }
    }
    std::printf("  %d PC image(s) x %u beats\n", npcs, nbeats);

    // one kernel invocation per PC -- this is literally what the CUs do
    std::vector<std::vector<ap_uint<DWIDTH> > > got(npcs);
    int sideband_err = 0;
    for (int i = 0; i < npcs; ++i) {
        hls::stream<pkt> s;
        krnl_mm2s(&img[i][0], s, nbeats);

        if (s.size() != nbeats) {
            std::printf("  ERROR: PC%d produced %u beats, expected %u\n",
                        i, (unsigned)s.size(), nbeats);
            return 1;
        }
        for (unsigned k = 0; k < nbeats; ++k) {
            pkt b = s.read();
            got[i].push_back(b.data);

            const bool want_last = (k == nbeats - 1);
            if ((bool)b.last != want_last && sideband_err < 5) {
                std::printf("  TLAST ERROR: PC%d beat %u has last=%d, expected %d\n",
                            i, k, (int)b.last, (int)want_last);
                ++sideband_err;
            }
            if (b.keep != ap_uint<DWIDTH / 8>(-1) && sideband_err < 5) {
                std::printf("  TKEEP ERROR: PC%d beat %u keep is not all ones\n", i, k);
                ++sideband_err;
            }
        }
    }

    // rebuild the bus line and compare to the .hex the RTL was verified against
    char hp[1024];
    std::snprintf(hp, sizeof(hp), "%s/%s", EMU_DIR, hexname);
    std::vector<std::string> want;
    if (!load_hex(hp, want)) return 1;

    if (want.size() != nbeats) {
        std::printf("  ERROR: %s has %u lines, images have %u beats\n",
                    hexname, (unsigned)want.size(), nbeats);
        return 1;
    }

    int bad = 0;
    for (unsigned k = 0; k < nbeats; ++k) {
        std::string line;
        for (int i = npcs - 1; i >= 0; --i) line += pc_hex(got[i][k]);
        if (line != want[k]) {
            if (++bad <= 3) {
                std::printf("  MISMATCH beat %u\n    got  %s\n    want %s\n",
                            k, line.c_str(), want[k].c_str());
            }
        }
    }

    if (bad || sideband_err) {
        std::printf("  FAIL: %d data beat(s) differ, %d sideband error(s)\n",
                    bad, sideband_err);
        return 1;
    }
    std::printf("  PASS: %u beats reproduce %s exactly, TLAST/TKEEP correct\n",
                nbeats, hexname);
    return 0;
}

int main() {
    std::printf("tb_mm2s -- reproducing the RTL stimulus out of HBM images\n");
    std::printf("EMU_DIR = %s\n\n", EMU_DIR);

    int rc = 0;
    rc |= check_stream("weights (8 PCs)", "weights_pc", 8, "weights.hex");
    std::printf("\n");
    rc |= check_stream("activations (2 PCs)", "act_pc", 2, "activations.hex");

    std::printf("\n=== %s ===\n", rc ? "FAIL" : "PASS");
    return rc;
}
