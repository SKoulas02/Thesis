// ---------------------------------------------------------------------------
// krnl_gemv_dense.v -- Vitis RTL kernel top for the DENSE GEMV engine.
//
// This is the RTL Kernel Wizard's generated top with ONE change, exactly where
// the generated comments invite it: the placeholder `krnl_gemv_dense_example`
// instance is replaced by `dense_gemv_axis` (the AXIS splitter wrapper around
// the verified `dense_gemv`).
//
// The module name, parameters and ports are UNCHANGED from the generated file
// -- the wizard owns those, and package_xo infers the 14 AXI4-Stream interfaces
// from them. Only the body between the "Add kernel logic here" markers differs.
//
// Mixed-language: this Verilog top instantiates a VHDL entity. Vivado resolves
// that by entity name in a mixed-language project; `dense_gemv_axis`'s generics
// all default to the values this kernel needs (PC_WIDTH=256, W_PCS=8, A_PCS=2,
// C_PCS=4), so no generic mapping is required or possible from Verilog here.
//
// Regenerate note: if the wizard config changes (stream count, widths), re-run
// gen_xo_dense.tcl and re-apply this same one-block substitution.
// ---------------------------------------------------------------------------

`default_nettype none
`timescale 1 ns / 1 ps
// Top level of the kernel. Do not modify module name, parameters or ports.
module krnl_gemv_dense #(
  parameter integer C_S_AXIS_W0_TDATA_WIDTH = 256,
  parameter integer C_S_AXIS_W1_TDATA_WIDTH = 256,
  parameter integer C_S_AXIS_W2_TDATA_WIDTH = 256,
  parameter integer C_S_AXIS_W3_TDATA_WIDTH = 256,
  parameter integer C_S_AXIS_W4_TDATA_WIDTH = 256,
  parameter integer C_S_AXIS_W5_TDATA_WIDTH = 256,
  parameter integer C_S_AXIS_W6_TDATA_WIDTH = 256,
  parameter integer C_S_AXIS_W7_TDATA_WIDTH = 256,
  parameter integer C_S_AXIS_A0_TDATA_WIDTH = 256,
  parameter integer C_S_AXIS_A1_TDATA_WIDTH = 256,
  parameter integer C_M_AXIS_C0_TDATA_WIDTH = 256,
  parameter integer C_M_AXIS_C1_TDATA_WIDTH = 256,
  parameter integer C_M_AXIS_C2_TDATA_WIDTH = 256,
  parameter integer C_M_AXIS_C3_TDATA_WIDTH = 256
)
(
  // System Signals
  input  wire                                 ap_clk          ,
  input  wire                                 ap_rst_n        ,
  // AXI4-Stream (slave) interface s_axis_w0
  input  wire                                 s_axis_w0_tvalid,
  output wire                                 s_axis_w0_tready,
  input  wire [C_S_AXIS_W0_TDATA_WIDTH-1:0]   s_axis_w0_tdata ,
  input  wire [C_S_AXIS_W0_TDATA_WIDTH/8-1:0] s_axis_w0_tkeep ,
  input  wire                                 s_axis_w0_tlast ,
  // AXI4-Stream (slave) interface s_axis_w1
  input  wire                                 s_axis_w1_tvalid,
  output wire                                 s_axis_w1_tready,
  input  wire [C_S_AXIS_W1_TDATA_WIDTH-1:0]   s_axis_w1_tdata ,
  input  wire [C_S_AXIS_W1_TDATA_WIDTH/8-1:0] s_axis_w1_tkeep ,
  input  wire                                 s_axis_w1_tlast ,
  // AXI4-Stream (slave) interface s_axis_w2
  input  wire                                 s_axis_w2_tvalid,
  output wire                                 s_axis_w2_tready,
  input  wire [C_S_AXIS_W2_TDATA_WIDTH-1:0]   s_axis_w2_tdata ,
  input  wire [C_S_AXIS_W2_TDATA_WIDTH/8-1:0] s_axis_w2_tkeep ,
  input  wire                                 s_axis_w2_tlast ,
  // AXI4-Stream (slave) interface s_axis_w3
  input  wire                                 s_axis_w3_tvalid,
  output wire                                 s_axis_w3_tready,
  input  wire [C_S_AXIS_W3_TDATA_WIDTH-1:0]   s_axis_w3_tdata ,
  input  wire [C_S_AXIS_W3_TDATA_WIDTH/8-1:0] s_axis_w3_tkeep ,
  input  wire                                 s_axis_w3_tlast ,
  // AXI4-Stream (slave) interface s_axis_w4
  input  wire                                 s_axis_w4_tvalid,
  output wire                                 s_axis_w4_tready,
  input  wire [C_S_AXIS_W4_TDATA_WIDTH-1:0]   s_axis_w4_tdata ,
  input  wire [C_S_AXIS_W4_TDATA_WIDTH/8-1:0] s_axis_w4_tkeep ,
  input  wire                                 s_axis_w4_tlast ,
  // AXI4-Stream (slave) interface s_axis_w5
  input  wire                                 s_axis_w5_tvalid,
  output wire                                 s_axis_w5_tready,
  input  wire [C_S_AXIS_W5_TDATA_WIDTH-1:0]   s_axis_w5_tdata ,
  input  wire [C_S_AXIS_W5_TDATA_WIDTH/8-1:0] s_axis_w5_tkeep ,
  input  wire                                 s_axis_w5_tlast ,
  // AXI4-Stream (slave) interface s_axis_w6
  input  wire                                 s_axis_w6_tvalid,
  output wire                                 s_axis_w6_tready,
  input  wire [C_S_AXIS_W6_TDATA_WIDTH-1:0]   s_axis_w6_tdata ,
  input  wire [C_S_AXIS_W6_TDATA_WIDTH/8-1:0] s_axis_w6_tkeep ,
  input  wire                                 s_axis_w6_tlast ,
  // AXI4-Stream (slave) interface s_axis_w7
  input  wire                                 s_axis_w7_tvalid,
  output wire                                 s_axis_w7_tready,
  input  wire [C_S_AXIS_W7_TDATA_WIDTH-1:0]   s_axis_w7_tdata ,
  input  wire [C_S_AXIS_W7_TDATA_WIDTH/8-1:0] s_axis_w7_tkeep ,
  input  wire                                 s_axis_w7_tlast ,
  // AXI4-Stream (slave) interface s_axis_a0
  input  wire                                 s_axis_a0_tvalid,
  output wire                                 s_axis_a0_tready,
  input  wire [C_S_AXIS_A0_TDATA_WIDTH-1:0]   s_axis_a0_tdata ,
  input  wire [C_S_AXIS_A0_TDATA_WIDTH/8-1:0] s_axis_a0_tkeep ,
  input  wire                                 s_axis_a0_tlast ,
  // AXI4-Stream (slave) interface s_axis_a1
  input  wire                                 s_axis_a1_tvalid,
  output wire                                 s_axis_a1_tready,
  input  wire [C_S_AXIS_A1_TDATA_WIDTH-1:0]   s_axis_a1_tdata ,
  input  wire [C_S_AXIS_A1_TDATA_WIDTH/8-1:0] s_axis_a1_tkeep ,
  input  wire                                 s_axis_a1_tlast ,
  // AXI4-Stream (master) interface m_axis_c0
  output wire                                 m_axis_c0_tvalid,
  input  wire                                 m_axis_c0_tready,
  output wire [C_M_AXIS_C0_TDATA_WIDTH-1:0]   m_axis_c0_tdata ,
  output wire [C_M_AXIS_C0_TDATA_WIDTH/8-1:0] m_axis_c0_tkeep ,
  output wire                                 m_axis_c0_tlast ,
  // AXI4-Stream (master) interface m_axis_c1
  output wire                                 m_axis_c1_tvalid,
  input  wire                                 m_axis_c1_tready,
  output wire [C_M_AXIS_C1_TDATA_WIDTH-1:0]   m_axis_c1_tdata ,
  output wire [C_M_AXIS_C1_TDATA_WIDTH/8-1:0] m_axis_c1_tkeep ,
  output wire                                 m_axis_c1_tlast ,
  // AXI4-Stream (master) interface m_axis_c2
  output wire                                 m_axis_c2_tvalid,
  input  wire                                 m_axis_c2_tready,
  output wire [C_M_AXIS_C2_TDATA_WIDTH-1:0]   m_axis_c2_tdata ,
  output wire [C_M_AXIS_C2_TDATA_WIDTH/8-1:0] m_axis_c2_tkeep ,
  output wire                                 m_axis_c2_tlast ,
  // AXI4-Stream (master) interface m_axis_c3
  output wire                                 m_axis_c3_tvalid,
  input  wire                                 m_axis_c3_tready,
  output wire [C_M_AXIS_C3_TDATA_WIDTH-1:0]   m_axis_c3_tdata ,
  output wire [C_M_AXIS_C3_TDATA_WIDTH/8-1:0] m_axis_c3_tkeep ,
  output wire                                 m_axis_c3_tlast
);

///////////////////////////////////////////////////////////////////////////////
// Begin control interface RTL.  Modifying not recommended.
///////////////////////////////////////////////////////////////////////////////
// (none -- this is an ap_ctrl_none free-running kernel: no s_axi_control, no
//  ap_start/ap_done. It runs from the moment the xclbin is programmed and
//  interacts with the rest of the system only through the streams above.)

///////////////////////////////////////////////////////////////////////////////
// Kernel logic.
///////////////////////////////////////////////////////////////////////////////
// The wizard's krnl_gemv_dense_example placeholder (which chained w0->c0,
// w1->c1, w2->c2, w3->c3 through vadd blocks and tied w4..w7/a0/a1 to NULL) is
// removed and replaced by the real engine. dense_gemv_axis is pure re-wiring
// around the verified dense_gemv -- see GEMV_Dense_Source/Design/dense_gemv_axis.vhd.

dense_gemv_axis inst_gemv (
  .ap_clk           ( ap_clk           ),
  .ap_rst_n         ( ap_rst_n         ),

  .s_axis_w0_tvalid ( s_axis_w0_tvalid ),
  .s_axis_w0_tready ( s_axis_w0_tready ),
  .s_axis_w0_tdata  ( s_axis_w0_tdata  ),
  .s_axis_w0_tkeep  ( s_axis_w0_tkeep  ),
  .s_axis_w0_tlast  ( s_axis_w0_tlast  ),

  .s_axis_w1_tvalid ( s_axis_w1_tvalid ),
  .s_axis_w1_tready ( s_axis_w1_tready ),
  .s_axis_w1_tdata  ( s_axis_w1_tdata  ),
  .s_axis_w1_tkeep  ( s_axis_w1_tkeep  ),
  .s_axis_w1_tlast  ( s_axis_w1_tlast  ),

  .s_axis_w2_tvalid ( s_axis_w2_tvalid ),
  .s_axis_w2_tready ( s_axis_w2_tready ),
  .s_axis_w2_tdata  ( s_axis_w2_tdata  ),
  .s_axis_w2_tkeep  ( s_axis_w2_tkeep  ),
  .s_axis_w2_tlast  ( s_axis_w2_tlast  ),

  .s_axis_w3_tvalid ( s_axis_w3_tvalid ),
  .s_axis_w3_tready ( s_axis_w3_tready ),
  .s_axis_w3_tdata  ( s_axis_w3_tdata  ),
  .s_axis_w3_tkeep  ( s_axis_w3_tkeep  ),
  .s_axis_w3_tlast  ( s_axis_w3_tlast  ),

  .s_axis_w4_tvalid ( s_axis_w4_tvalid ),
  .s_axis_w4_tready ( s_axis_w4_tready ),
  .s_axis_w4_tdata  ( s_axis_w4_tdata  ),
  .s_axis_w4_tkeep  ( s_axis_w4_tkeep  ),
  .s_axis_w4_tlast  ( s_axis_w4_tlast  ),

  .s_axis_w5_tvalid ( s_axis_w5_tvalid ),
  .s_axis_w5_tready ( s_axis_w5_tready ),
  .s_axis_w5_tdata  ( s_axis_w5_tdata  ),
  .s_axis_w5_tkeep  ( s_axis_w5_tkeep  ),
  .s_axis_w5_tlast  ( s_axis_w5_tlast  ),

  .s_axis_w6_tvalid ( s_axis_w6_tvalid ),
  .s_axis_w6_tready ( s_axis_w6_tready ),
  .s_axis_w6_tdata  ( s_axis_w6_tdata  ),
  .s_axis_w6_tkeep  ( s_axis_w6_tkeep  ),
  .s_axis_w6_tlast  ( s_axis_w6_tlast  ),

  .s_axis_w7_tvalid ( s_axis_w7_tvalid ),
  .s_axis_w7_tready ( s_axis_w7_tready ),
  .s_axis_w7_tdata  ( s_axis_w7_tdata  ),
  .s_axis_w7_tkeep  ( s_axis_w7_tkeep  ),
  .s_axis_w7_tlast  ( s_axis_w7_tlast  ),

  .s_axis_a0_tvalid ( s_axis_a0_tvalid ),
  .s_axis_a0_tready ( s_axis_a0_tready ),
  .s_axis_a0_tdata  ( s_axis_a0_tdata  ),
  .s_axis_a0_tkeep  ( s_axis_a0_tkeep  ),
  .s_axis_a0_tlast  ( s_axis_a0_tlast  ),

  .s_axis_a1_tvalid ( s_axis_a1_tvalid ),
  .s_axis_a1_tready ( s_axis_a1_tready ),
  .s_axis_a1_tdata  ( s_axis_a1_tdata  ),
  .s_axis_a1_tkeep  ( s_axis_a1_tkeep  ),
  .s_axis_a1_tlast  ( s_axis_a1_tlast  ),

  .m_axis_c0_tvalid ( m_axis_c0_tvalid ),
  .m_axis_c0_tready ( m_axis_c0_tready ),
  .m_axis_c0_tdata  ( m_axis_c0_tdata  ),
  .m_axis_c0_tkeep  ( m_axis_c0_tkeep  ),
  .m_axis_c0_tlast  ( m_axis_c0_tlast  ),

  .m_axis_c1_tvalid ( m_axis_c1_tvalid ),
  .m_axis_c1_tready ( m_axis_c1_tready ),
  .m_axis_c1_tdata  ( m_axis_c1_tdata  ),
  .m_axis_c1_tkeep  ( m_axis_c1_tkeep  ),
  .m_axis_c1_tlast  ( m_axis_c1_tlast  ),

  .m_axis_c2_tvalid ( m_axis_c2_tvalid ),
  .m_axis_c2_tready ( m_axis_c2_tready ),
  .m_axis_c2_tdata  ( m_axis_c2_tdata  ),
  .m_axis_c2_tkeep  ( m_axis_c2_tkeep  ),
  .m_axis_c2_tlast  ( m_axis_c2_tlast  ),

  .m_axis_c3_tvalid ( m_axis_c3_tvalid ),
  .m_axis_c3_tready ( m_axis_c3_tready ),
  .m_axis_c3_tdata  ( m_axis_c3_tdata  ),
  .m_axis_c3_tkeep  ( m_axis_c3_tkeep  ),
  .m_axis_c3_tlast  ( m_axis_c3_tlast  )
);

endmodule
`default_nettype wire
