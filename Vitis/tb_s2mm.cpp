// ---------------------------------------------------------------------------
// tb_s2mm.cpp -- csim/cosim testbench for krnl_s2mm (INTEGRATION_STEPS.md S12).
//
// The write side has no stimulus file to reproduce, so this is a round trip:
// push a known pattern through the kernel and check the buffer it wrote.
//
// Two properties are worth asserting, and both are easy to get wrong:
//   1. every beat lands, in order, unmodified -- a PC that drops or reorders a
//      beat desyncs the 4-way output fork and corrupts whole rows;
//   2. termination is by COUNT, not TLAST. The kernel must stop after exactly
//      n_beats even when the stream still holds more, and must NOT stop early
//      on a TLAST. Both are checked below, because the real engine asserts
//      TLAST on its final beat and the host relies on the count alone.
// ---------------------------------------------------------------------------

#include <ap_int.h>
#include <ap_axi_sdata.h>
#include <hls_stream.h>

#include <cstdio>
#include <vector>

#define DWIDTH 256
typedef ap_axiu<DWIDTH, 0, 0, 0> pkt;

extern "C" void krnl_s2mm(hls::stream<pkt>& in, ap_uint<DWIDTH>* out,
                          unsigned int n_beats);

// distinct value per beat, spread across the full 256 bits so a truncated or
// byte-swapped path cannot accidentally match
static ap_uint<DWIDTH> pattern(unsigned k) {
    ap_uint<DWIDTH> v = 0;
    for (int w = 0; w < DWIDTH / 32; ++w)
        v.range(32 * w + 31, 32 * w) = (ap_uint<32>)(0xA5A50000u + k * 32u + w);
    return v;
}

int main() {
    std::printf("tb_s2mm -- stream to memory round trip\n\n");

    const unsigned N = 37;      // deliberately not a power of two
    const unsigned EXTRA = 5;   // beats beyond n_beats, must be left untouched

    hls::stream<pkt> s;
    for (unsigned k = 0; k < N + EXTRA; ++k) {
        pkt b;
        b.data = pattern(k);
        b.keep = -1;
        b.strb = -1;
        // TLAST early on purpose: the kernel must ignore it and keep counting.
        b.last = (k == N / 2) ? 1 : 0;
        s.write(b);
    }

    std::vector<ap_uint<DWIDTH> > buf(N + EXTRA, ap_uint<DWIDTH>(0));
    krnl_s2mm(s, &buf[0], N);

    int rc = 0;

    for (unsigned k = 0; k < N; ++k) {
        if (buf[k] != pattern(k)) {
            if (rc < 3) std::printf("  MISMATCH at beat %u\n", k);
            rc = 1;
        }
    }
    if (!rc) std::printf("  PASS: %u beats written in order, unmodified\n", N);

    // an early TLAST must not have stopped it short
    if (s.size() != EXTRA) {
        std::printf("  FAIL: %u beat(s) left in the stream, expected %u"
                    " -- kernel did not stop exactly at n_beats\n",
                    (unsigned)s.size(), EXTRA);
        rc = 1;
    } else {
        std::printf("  PASS: stopped at n_beats, ignoring the early TLAST\n");
    }

    // and it must not have written past n_beats
    for (unsigned k = N; k < N + EXTRA; ++k) {
        if (buf[k] != ap_uint<DWIDTH>(0)) {
            std::printf("  FAIL: wrote past n_beats at index %u\n", k);
            rc = 1;
            break;
        }
    }

    std::printf("\n=== %s ===\n", rc ? "FAIL" : "PASS");
    return rc;
}
