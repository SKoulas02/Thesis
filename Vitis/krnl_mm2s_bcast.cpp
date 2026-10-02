// ---------------------------------------------------------------------------
// krnl_mm2s_bcast.cpp -- HBM -> the SAME AXI4-Stream to N engines.  ONE
// pseudo-channel, ONE AXI master, N stream outputs.
//
// A COPY OF krnl_mm2s.cpp (2026-09-28), edited; krnl_mm2s.cpp itself is untouched
// and every existing build keeps using it. Only the shared-vector BROADCAST builds
// (multi_tenant/make_bcast_build.py) use this kernel, and only for the vector.
//
// WHY IT EXISTS. On the U280 every mover's m_axi takes one of the HBM subsystem's
// 32 kernel ports, and v++ never shares a port between movers, even movers that
// read the same channel. With one vector read by every engine (the MoE case), a
// mover pair per engine wastes 2 ports per engine: 6 x 4x4 needed 36, 7 x 4x4 42,
// 2 x 8x8 34, and 6 x 4x4 failed to link ("All 33 connections are used"). One
// broadcast pair for the whole card needs 2 ports:
//     movers = n x (weights + indices + outputs) + 2
//     6 x 4x4 -> 26    7 x 4x4 -> 30    2 x 8x8 -> 32
//
// WHAT IT DOES. Exactly what krnl_mm2s does -- read n_beats 256-bit words, one per
// clock, TKEEP/TSTRB all ones, TLAST on the final beat -- but every beat goes to
// every output in the same clock. Output k is wired (stream_connect) to engine k's
// s_axis_a0 (the a0 CU, reading HBM[0]) or s_axis_a1 (the a1 CU, HBM[1]).
//
// ONE KERNEL PER ENGINE COUNT, NOT A PARAMETER. The number of AXIS ports is part of
// the kernel's interface, so each engine count is its own top function, compiled on
// its own (v++ -c -k krnl_mm2s_bcast6 ...). A bitstream linked with the wrong count
// fails at link time ("kernel not found" or a missing port) instead of leaving an
// engine without its vector. To add a count, copy one function and add/remove the
// outN lines -- nothing else changes.
//
// STALLS. A blocking write to an output whose engine is not ready stalls ALL
// outputs (one pipeline). It never happens in practice: each engine's activation
// ingress FIFO is 512 deep per PC and a vector is at most 256 beats (8192
// elements), and in lockstep every engine has emptied it before the next layer's
// vector is sent. What IS required: EVERY engine the kernel feeds must run every
// calculation -- an engine left out would keep a stale vector in its FIFO and
// misalign the next one. The broadcast host enforces it (all tenants, equal
// calculation counts, lockstep).
//
// Arguments (host setArg): 0 = in (buffer), 1..N = the streams (never set by the
// host), N+1 = n_beats.
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

// ---- 2 engines (2 x 8x8) -------------------------------------------------
void krnl_mm2s_bcast2(const ap_uint<DWIDTH>* in,
                      hls::stream<pkt>& out0, hls::stream<pkt>& out1,
                      unsigned int n_beats) {
#pragma HLS INTERFACE m_axi port = in offset = slave bundle = gmem \
    max_read_burst_length = 64 num_read_outstanding = 32
#pragma HLS INTERFACE axis port = out0
#pragma HLS INTERFACE axis port = out1
#pragma HLS INTERFACE s_axilite port = in bundle = control
#pragma HLS INTERFACE s_axilite port = n_beats bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

bcast:
    for (unsigned int k = 0; k < n_beats; ++k) {
#pragma HLS PIPELINE II = 1
        const pkt b = make_beat(in[k], k == n_beats - 1);
        out0.write(b);
        out1.write(b);
    }
}

// ---- 6 engines (6 x 4x4) -------------------------------------------------
void krnl_mm2s_bcast6(const ap_uint<DWIDTH>* in,
                      hls::stream<pkt>& out0, hls::stream<pkt>& out1,
                      hls::stream<pkt>& out2, hls::stream<pkt>& out3,
                      hls::stream<pkt>& out4, hls::stream<pkt>& out5,
                      unsigned int n_beats) {
#pragma HLS INTERFACE m_axi port = in offset = slave bundle = gmem \
    max_read_burst_length = 64 num_read_outstanding = 32
#pragma HLS INTERFACE axis port = out0
#pragma HLS INTERFACE axis port = out1
#pragma HLS INTERFACE axis port = out2
#pragma HLS INTERFACE axis port = out3
#pragma HLS INTERFACE axis port = out4
#pragma HLS INTERFACE axis port = out5
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
        out3.write(b);
        out4.write(b);
        out5.write(b);
    }
}

// ---- 7 engines (7 x 4x4) -------------------------------------------------
void krnl_mm2s_bcast7(const ap_uint<DWIDTH>* in,
                      hls::stream<pkt>& out0, hls::stream<pkt>& out1,
                      hls::stream<pkt>& out2, hls::stream<pkt>& out3,
                      hls::stream<pkt>& out4, hls::stream<pkt>& out5,
                      hls::stream<pkt>& out6,
                      unsigned int n_beats) {
#pragma HLS INTERFACE m_axi port = in offset = slave bundle = gmem \
    max_read_burst_length = 64 num_read_outstanding = 32
#pragma HLS INTERFACE axis port = out0
#pragma HLS INTERFACE axis port = out1
#pragma HLS INTERFACE axis port = out2
#pragma HLS INTERFACE axis port = out3
#pragma HLS INTERFACE axis port = out4
#pragma HLS INTERFACE axis port = out5
#pragma HLS INTERFACE axis port = out6
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
        out3.write(b);
        out4.write(b);
        out5.write(b);
        out6.write(b);
    }
}

} // extern "C"
