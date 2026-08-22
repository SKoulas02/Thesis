// ---------------------------------------------------------------------------
// krnl_mm2s.cpp -- HBM -> one AXI4-Stream.  ONE pseudo-channel per compute unit.
//
// This single kernel serves EVERY input stream of both architectures. It is
// instantiated by the linker (nk=) once per PC:
//     dense  : 10 CUs  (8 weight + 2 activation)
//     sparse : 13 CUs  (8 weight + 3 index + 2 activation)
//
// Why one kernel covers weights, indices and activations alike: every
// difference between those streams is either a BEAT COUNT (an argument) or
// BUFFER CONTENT (packed by the Python generator), never behaviour.
//   * weight TLAST  = end of the matrix   -> last beat of this CU's loop
//   * activation TLAST = end of the vector -> last beat of this CU's loop
//   The two markers differ in MEANING, not in generation. The activation CU
//   simply has a much smaller n_beats and finishes early, which is exactly the
//   "channel stops" behaviour the replay buffer in the engine expects.
//   * the 2-bit sparsity code rides in index PC2 padding [641:640] -- buffer
//   content written by the host, invisible here.
//
// Shape follows krnl_mm2s.cpp in the AMD 2021.1 example
// rtl_streaming_free_running_k2k: one m_axi, one axis, ap_ctrl_hs, a single
// pipelined loop, TLAST from the loop bound. No DATAFLOW -- there is only one
// stream per CU, so there is nothing to overlap.
// ---------------------------------------------------------------------------

#include <ap_int.h>
#include <ap_axi_sdata.h>
#include <hls_stream.h>

#define DWIDTH 256
typedef ap_axiu<DWIDTH, 0, 0, 0> pkt;

extern "C" {

void krnl_mm2s(const ap_uint<DWIDTH>* in, hls::stream<pkt>& out, unsigned int n_beats) {
#pragma HLS INTERFACE m_axi port = in offset = slave bundle = gmem \
    max_read_burst_length = 64 num_read_outstanding = 32
#pragma HLS INTERFACE axis port = out
#pragma HLS INTERFACE s_axilite port = in bundle = control
#pragma HLS INTERFACE s_axilite port = n_beats bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

mm2s:
    for (unsigned int k = 0; k < n_beats; ++k) {
#pragma HLS PIPELINE II = 1
        pkt b;
        b.data = in[k];
        b.keep = -1;   // all ones: every beat is a full 256-bit PC word.
        b.strb = -1;   // UG1393 forbids all-zero TKEEP and requires all-ones
                       // whenever TLAST is 0; this engine has no ragged tails.
        b.last = (k == n_beats - 1) ? 1 : 0;
        out.write(b);
    }
}

} // extern "C"
