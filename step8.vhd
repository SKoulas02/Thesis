// This is a generated file. Use and modify at your own risk.
//////////////////////////////////////////////////////////////////////////////// 
// default_nettype of none prevents implicit wire declaration.
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
  //  Note: A minimum subset of AXI4 memory mapped signals are declared.  AXI
  // signals omitted from these interfaces are automatically inferred with the
  // optimal values for Xilinx accleration platforms.  This allows Xilinx AXI4 Interconnects
  // within the system to be optimized by removing logic for AXI4 protocol
  // features that are not necessary. When adapting AXI4 masters within the RTL
  // kernel that have signals not declared below, it is suitable to add the
  // signals to the declarations below to connect them to the AXI4 Master.
  // 
  // List of ommited signals - effect
  // -------------------------------
  // ID - Transaction ID are used for multithreading and out of order
  // transactions.  This increases complexity. This saves logic and increases Fmax
  // in the system when ommited.
  // SIZE - Default value is log2(data width in bytes). Needed for subsize bursts.
  // This saves logic and increases Fmax in the system when ommited.
  // BURST - Default value (0b01) is incremental.  Wrap and fixed bursts are not
  // recommended. This saves logic and increases Fmax in the system when ommited.
  // LOCK - Not supported in AXI4
  // CACHE - Default value (0b0011) allows modifiable transactions. No benefit to
  // changing this.
  // PROT - Has no effect in current acceleration platforms.
  // QOS - Has no effect in current acceleration platforms.
  // REGION - Has no effect in current acceleration platforms.
  // USER - Has no effect in current acceleration platforms.
  // RESP - Not useful in most acceleration platforms.
  // 
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
// Local Parameters
///////////////////////////////////////////////////////////////////////////////

///////////////////////////////////////////////////////////////////////////////
// Wires and Variables
///////////////////////////////////////////////////////////////////////////////
(* DONT_TOUCH = "yes" *)
reg                                 areset                         = 1'b0;

// Register and invert reset signal.
always @(posedge ap_clk) begin
  areset <= ~ap_rst_n;
end

///////////////////////////////////////////////////////////////////////////////
// Begin control interface RTL.  Modifying not recommended.
///////////////////////////////////////////////////////////////////////////////


///////////////////////////////////////////////////////////////////////////////
// Add kernel logic here.  Modify/remove example code as necessary.
///////////////////////////////////////////////////////////////////////////////

// Example RTL block.  Remove to insert custom logic.
krnl_gemv_dense_example #(
  .C_S_AXIS_W0_TDATA_WIDTH ( C_S_AXIS_W0_TDATA_WIDTH ),
  .C_S_AXIS_W1_TDATA_WIDTH ( C_S_AXIS_W1_TDATA_WIDTH ),
  .C_S_AXIS_W2_TDATA_WIDTH ( C_S_AXIS_W2_TDATA_WIDTH ),
  .C_S_AXIS_W3_TDATA_WIDTH ( C_S_AXIS_W3_TDATA_WIDTH ),
  .C_S_AXIS_W4_TDATA_WIDTH ( C_S_AXIS_W4_TDATA_WIDTH ),
  .C_S_AXIS_W5_TDATA_WIDTH ( C_S_AXIS_W5_TDATA_WIDTH ),
  .C_S_AXIS_W6_TDATA_WIDTH ( C_S_AXIS_W6_TDATA_WIDTH ),
  .C_S_AXIS_W7_TDATA_WIDTH ( C_S_AXIS_W7_TDATA_WIDTH ),
  .C_S_AXIS_A0_TDATA_WIDTH ( C_S_AXIS_A0_TDATA_WIDTH ),
  .C_S_AXIS_A1_TDATA_WIDTH ( C_S_AXIS_A1_TDATA_WIDTH ),
  .C_M_AXIS_C0_TDATA_WIDTH ( C_M_AXIS_C0_TDATA_WIDTH ),
  .C_M_AXIS_C1_TDATA_WIDTH ( C_M_AXIS_C1_TDATA_WIDTH ),
  .C_M_AXIS_C2_TDATA_WIDTH ( C_M_AXIS_C2_TDATA_WIDTH ),
  .C_M_AXIS_C3_TDATA_WIDTH ( C_M_AXIS_C3_TDATA_WIDTH )
)
inst_example (
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

