// ---------------------------------------------------------------------------
// krnl_s2mm.cpp -- one AXI4-Stream -> HBM.  ONE pseudo-channel per compute unit.
//
// Instantiated 4x by the linker (nk=), one per output PC, for both
// architectures. The 8 C Cores emit one co-valid 1024-bit beat which the c_fifo
// fork splits across these four channels.
//
// TERMINATION IS COUNT-BASED, and TLAST is deliberately never inspected --
// exactly as in the AMD 2021.1 reference krnl_s2mm.cpp. These four CUs are what
// the host waits on: each counts n_beats, returns, and that return raises its
// ap_done. The free-running compute kernel between the movers never starts or
// stops, so ap_done on the sinks IS the end of the calculation.
//
// (The engine does drive a meaningful TLAST -- the end-of-calc marker on the
// final beat -- but the host already knows the beat count, so consuming it here
// would add a second, redundant source of truth about when to stop.)
// ---------------------------------------------------------------------------

#include <ap_int.h>
#include <ap_axi_sdata.h>
#include <hls_stream.h>

#define DWIDTH 256
typedef ap_axiu<DWIDTH, 0, 0, 0> pkt;

extern "C" {

void krnl_s2mm(hls::stream<pkt>& in, ap_uint<DWIDTH>* out, unsigned int n_beats) {
#pragma HLS INTERFACE m_axi port = out offset = slave bundle = gmem \
    max_write_burst_length = 64 num_write_outstanding = 32
#pragma HLS INTERFACE axis port = in
#pragma HLS INTERFACE s_axilite port = out bundle = control
#pragma HLS INTERFACE s_axilite port = n_beats bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

s2mm:
    for (unsigned int k = 0; k < n_beats; ++k) {
#pragma HLS PIPELINE II = 1
        out[k] = in.read().data;
    }
}

} // extern "C"
