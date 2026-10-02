// ---------------------------------------------------------------------------
// krnl_mm2s_bcast3.cpp -- HBM -> the SAME AXI4-Stream to 3 engines (x3_bcast).
//
// A COPY OF krnl_mm2s_bcast.cpp (2026-09-29), edited to the one engine count it lacks;
// krnl_mm2s_bcast.cpp is untouched -- its krnl_mm2s_bcast2/6/7 .xo files are compiled and
// verified (csim PASS, Final II = 1 on the server), and recompiling that file for a fourth
// function would only put them at risk. Everything krnl_mm2s_bcast.cpp says about WHY the
// broadcast mover exists, what it does, and when it can stall, holds here unchanged.
//
// Used by the 3 x 4x4 broadcast build (multi_tenant/x3_bcast): the broadcast twin of
// 3 x 4x4 (own vector per engine, 18 movers) and 3 x 4x4 shared (one vector copy, 18
// movers). Here: one vector copy AND one mover pair -> 3 x (2 + 1 + 1) + 2 = 14 movers.
//
// Arguments (host setArg): 0 = in (buffer), 1..3 = the streams (never set by the host),
// 4 = n_beats.
//
// Compile: v++ -c -t hw -k krnl_mm2s_bcast3 -o krnl_mm2s_bcast3.hw.xo krnl_mm2s_bcast3.cpp
//          (multi_tenant/make_bcast3_xo.sh does it, after the C simulation passes)
// ---------------------------------------------------------------------------

#include <ap_int.h>
#include <ap_axi_sdata.h>
#include <hls_stream.h>

#define DWIDTH 256
typedef ap_axiu<DWIDTH, 0, 0, 0> pkt;

// One beat, exactly as krnl_mm2s builds it.
static pkt make_beat(const ap_uint<DWIDTH>& d, bool last) {
#pragma HLS INLINE
    pkt b;
    b.data = d;
    b.keep = -1;   // all ones: every beat is a full 256-bit PC word.
    b.strb = -1;   // UG1393 forbids all-zero TKEEP and requires all-ones
                   // whenever TLAST is 0; this engine has no ragged tails.
    b.last = last ? 1 : 0;
    return b;
}

extern "C" {

// ---- 3 engines (3 x 4x4) -------------------------------------------------
void krnl_mm2s_bcast3(const ap_uint<DWIDTH>* in,
                      hls::stream<pkt>& out0, hls::stream<pkt>& out1,
                      hls::stream<pkt>& out2,
                      unsigned int n_beats) {
#pragma HLS INTERFACE m_axi port = in offset = slave bundle = gmem \
    max_read_burst_length = 64 num_read_outstanding = 32
#pragma HLS INTERFACE axis port = out0
#pragma HLS INTERFACE axis port = out1
#pragma HLS INTERFACE axis port = out2
#pragma HLS INTERFACE s_axilite port = in bundle = control
#pragma HLS INTERFACE s_axilite port = n_beats bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

bcast:
    for (unsigned int k = 0; k < n_beats; ++k) {
#pragma HLS PIPELINE II = 1
        const pkt b = make_beat(in[k], k == n_beats - 1);
        out0.write(b);
        out1.write(b);
        out2.write(b);
    }
}

} // extern "C"
