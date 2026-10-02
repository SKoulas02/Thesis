// ---------------------------------------------------------------------------
// tb_mm2s_bcast.cpp -- csim testbench for krnl_mm2s_bcast{2,6,7}.
//
// A COPY OF tb_mm2s.cpp (2026-09-28), edited; tb_mm2s.cpp is untouched.
//
// THE POINT is the same as tb_mm2s: the mover must reproduce, out of HBM, the exact
// beats the RTL was verified against. For a broadcast mover that has to hold on
// EVERY output, and the outputs must be identical to one another beat for beat --
// an engine that got a different (shifted, truncated, reordered) copy of the
// vector would compute a wrong answer with no error anywhere.
//
// Three checks, on every kernel (2, 6 and 7 outputs):
//   1. activations.hex -- the RTL-verified vector. It is split into its two PC
//      images exactly as hex_to_bin.py does (PC0 = bits [255:0] = the RIGHT half
//      of each line), each image goes through the kernel as the a0 / a1 CU would
//      read it, and on every output the two PCs must rebuild the .hex line for line.
//   2. synthetic vectors of 1, 2, 17, 255 and 256 beats (256 = 8192 elements, the
//      largest vector the engine takes), distinct pseudo-random data per beat.
//   3. every output: exactly n_beats beats, TLAST on the last beat and no other,
//      TKEEP and TSTRB all ones on every beat.
//
// Paths: activations.hex is read from EMU_DIR (the server layout by default);
// override at compile time with  -DEMU_DIR=\"/some/other/Emulation\"
// -DONLY_N=<2|6|7> tests that one kernel (run_hls_bcast.tcl, one project per kernel).
//
// Local run (no HLS tool needed -- the kernel is plain C++ against the HLS headers):
//   g++ -std=c++14 -I<Vitis_HLS>/include tb_mm2s_bcast.cpp krnl_mm2s_bcast.cpp -o tb_bcast
// ---------------------------------------------------------------------------

#include <ap_int.h>
#include <ap_axi_sdata.h>
#include <hls_stream.h>

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#ifndef EMU_DIR
#define EMU_DIR "/home/skoulas/GEMV_Sparse/GEMV_4.0_Source/Emulation"
#endif

#define DWIDTH 256
typedef ap_axiu<DWIDTH, 0, 0, 0> pkt;
typedef ap_uint<DWIDTH> word;

extern "C" void krnl_mm2s_bcast2(const word* in, hls::stream<pkt>& out0,
                                 hls::stream<pkt>& out1, unsigned int n_beats);
extern "C" void krnl_mm2s_bcast6(const word* in, hls::stream<pkt>& out0,
                                 hls::stream<pkt>& out1, hls::stream<pkt>& out2,
                                 hls::stream<pkt>& out3, hls::stream<pkt>& out4,
                                 hls::stream<pkt>& out5, unsigned int n_beats);
extern "C" void krnl_mm2s_bcast7(const word* in, hls::stream<pkt>& out0,
                                 hls::stream<pkt>& out1, hls::stream<pkt>& out2,
                                 hls::stream<pkt>& out3, hls::stream<pkt>& out4,
                                 hls::stream<pkt>& out5, hls::stream<pkt>& out6,
                                 unsigned int n_beats);

static const int MAX_OUT = 7;

// ---- helpers ---------------------------------------------------------------

// 256-bit word -> 64 uppercase hex chars, MSB first (as in tb_mm2s).
static std::string pc_hex(const word& v) {
    static const char* D = "0123456789ABCDEF";
    std::string s;
    for (int n = DWIDTH / 4 - 1; n >= 0; --n)
        s += D[(unsigned)v.range(4 * n + 3, 4 * n)];
    return s;
}

// 64 hex chars, MSB first -> 256-bit word.
static word hex_pc(const std::string& s) {
    word v = 0;
    for (int n = 0; n < DWIDTH / 4; ++n) {
        const char c = s[DWIDTH / 4 - 1 - n];
        const unsigned d = (c >= '0' && c <= '9') ? c - '0' : (c & 0x5F) - 'A' + 10;
        v.range(4 * n + 3, 4 * n) = d;
    }
    return v;
}

// Read a .hex file: one bus beat per line, uppercase, fixed width (as in tb_mm2s).
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

// Run the kernel with `nout` outputs over one PC image.
static void run_kernel(int nout, const std::vector<word>& img, hls::stream<pkt>* s) {
    const unsigned n = (unsigned)img.size();
    if (nout == 2)
        krnl_mm2s_bcast2(&img[0], s[0], s[1], n);
    else if (nout == 6)
        krnl_mm2s_bcast6(&img[0], s[0], s[1], s[2], s[3], s[4], s[5], n);
    else
        krnl_mm2s_bcast7(&img[0], s[0], s[1], s[2], s[3], s[4], s[5], s[6], n);
}

// Drain every output of one run, checking the count and the sidebands.
// -> got[o][k] = beat k of output o. Returns the number of errors.
static int drain(int nout, unsigned nbeats, hls::stream<pkt>* s, const char* label,
                 std::vector<std::vector<word> >& got) {
    int err = 0;
    got.assign(nout, std::vector<word>());
    for (int o = 0; o < nout; ++o) {
        if (s[o].size() != nbeats) {
            std::printf("  ERROR %s: output %d produced %u beats, expected %u\n", label, o,
                        (unsigned)s[o].size(), nbeats);
            ++err;
        }
        unsigned k = 0;
        while (!s[o].empty()) {
            const pkt b = s[o].read();
            got[o].push_back(b.data);
            const bool want_last = (k == nbeats - 1);
            if ((bool)b.last != want_last && err < 8) {
                std::printf("  TLAST ERROR %s: output %d beat %u has last=%d, expected %d\n",
                            label, o, k, (int)b.last, (int)want_last);
                ++err;
            }
            if (b.keep != ap_uint<DWIDTH / 8>(-1) && err < 8) {
                std::printf("  TKEEP ERROR %s: output %d beat %u keep is not all ones\n",
                            label, o, k);
                ++err;
            }
            if (b.strb != ap_uint<DWIDTH / 8>(-1) && err < 8) {
                std::printf("  TSTRB ERROR %s: output %d beat %u strb is not all ones\n",
                            label, o, k);
                ++err;
            }
            ++k;
        }
    }
    return err;
}

// Every output must equal the input image, beat for beat.
static int same_as_input(int nout, const std::vector<word>& img,
                         const std::vector<std::vector<word> >& got, const char* label) {
    int bad = 0;
    for (int o = 0; o < nout; ++o) {
        if (got[o].size() != img.size()) continue;       // already reported by drain()
        for (size_t k = 0; k < img.size(); ++k) {
            if (got[o][k] != img[k] && ++bad <= 3)
                std::printf("  MISMATCH %s: output %d beat %u\n    got  %s\n    want %s\n",
                            label, o, (unsigned)k, pc_hex(got[o][k]).c_str(),
                            pc_hex(img[k]).c_str());
        }
    }
    return bad;
}

// ---- check 1: the RTL-verified vector ---------------------------------------
static int check_hex(int nout) {
    char hp[1024];
    std::snprintf(hp, sizeof(hp), "%s/activations.hex", EMU_DIR);
    std::vector<std::string> lines;
    if (!load_hex(hp, lines)) return 1;
    // PC i occupies bits [256i+255 : 256i], so the hex line is PC1 then PC0.
    std::vector<word> pc[2];
    for (size_t k = 0; k < lines.size(); ++k) {
        if (lines[k].size() != 2 * DWIDTH / 4) {
            std::printf("  ERROR: activations.hex line %u has %u hex digits, expected %d\n",
                        (unsigned)k, (unsigned)lines[k].size(), 2 * DWIDTH / 4);
            return 1;
        }
        pc[1].push_back(hex_pc(lines[k].substr(0, DWIDTH / 4)));
        pc[0].push_back(hex_pc(lines[k].substr(DWIDTH / 4)));
    }
    const unsigned nbeats = (unsigned)lines.size();

    // one kernel run per PC -- the a0 CU and the a1 CU
    std::vector<std::vector<word> > got[2];
    int err = 0;
    for (int p = 0; p < 2; ++p) {
        hls::stream<pkt> s[MAX_OUT];
        run_kernel(nout, pc[p], s);
        err += drain(nout, nbeats, s, p ? "hex a1" : "hex a0", got[p]);
    }
    // every output o: its a0 and a1 beats rebuild the .hex the RTL was verified against
    int bad = 0;
    for (int o = 0; o < nout; ++o) {
        if (got[0][o].size() != nbeats || got[1][o].size() != nbeats) continue;
        for (unsigned k = 0; k < nbeats; ++k) {
            const std::string line = pc_hex(got[1][o][k]) + pc_hex(got[0][o][k]);
            if (line != lines[k] && ++bad <= 3)
                std::printf("  MISMATCH engine %d beat %u\n    got  %s\n    want %s\n", o, k,
                            line.c_str(), lines[k].c_str());
        }
    }
    if (err || bad) {
        std::printf("  FAIL activations.hex: %d beat(s) differ, %d count/sideband error(s)\n",
                    bad, err);
        return 1;
    }
    std::printf("  PASS activations.hex: %u beats x 2 PCs reproduced on all %d outputs\n",
                nbeats, nout);
    return 0;
}

// ---- check 2: synthetic vectors ---------------------------------------------
static int check_synthetic(int nout, unsigned nbeats, unsigned seed) {
    std::vector<word> img(nbeats);
    unsigned x = seed;
    for (unsigned k = 0; k < nbeats; ++k) {
        word v = 0;
        for (int i = 0; i < DWIDTH / 32; ++i) {
            x = x * 1664525u + 1013904223u;             // LCG: distinct data per beat
            v.range(32 * i + 31, 32 * i) = x;
        }
        img[k] = v;
    }
    char label[64];
    std::snprintf(label, sizeof(label), "%u beats", nbeats);
    hls::stream<pkt> s[MAX_OUT];
    run_kernel(nout, img, s);
    std::vector<std::vector<word> > got;
    const int err = drain(nout, nbeats, s, label, got);
    const int bad = same_as_input(nout, img, got, label);
    if (err || bad) {
        std::printf("  FAIL %s: %d beat(s) differ, %d count/sideband error(s)\n", label, bad,
                    err);
        return 1;
    }
    return 0;
}

int main() {
    std::printf("tb_mm2s_bcast -- one HBM image, the same stream on every output\n");
    std::printf("EMU_DIR = %s\n", EMU_DIR);

    // -DONLY_N=6: test one kernel only (each HLS project has one top, and cosim
    // replaces only that one with its RTL -- run_hls_bcast.tcl sets it per project)
#ifdef ONLY_N
    const int counts[] = { ONLY_N };
#else
    const int counts[] = { 2, 6, 7 };
#endif
    const int ncounts = (int)(sizeof(counts) / sizeof(counts[0]));
    const unsigned sizes[] = { 1, 2, 17, 255, 256 };
    int rc = 0;
    for (int c = 0; c < ncounts; ++c) {
        const int nout = counts[c];
        std::printf("\nkrnl_mm2s_bcast%d (%d outputs):\n", nout, nout);
        rc |= check_hex(nout);
        int syn = 0;
        for (int i = 0; i < 5; ++i) syn |= check_synthetic(nout, sizes[i], 7u * nout + i);
        if (!syn)
            std::printf("  PASS synthetic vectors of 1, 2, 17, 255, 256 beats on all %d outputs\n",
                        nout);
        rc |= syn;
    }
    std::printf("\n=== %s ===\n", rc ? "FAIL" : "PASS");
    return rc;
}
