"""Generate the per-configuration build artefacts for the six family winners.

    python family_builds/make_family_build.py            # all six
    python family_builds/make_family_build.py 4x4        # just one

Writes family_builds/<tag>/ containing everything that DIFFERS between one
family's bitstream build and another's:

    Design/two2N_axis.vhd      the AXIS splitter, W_PCS+IND_PCS+A_PCS slaves
                               and C_PCS masters, generics defaulted to THIS
                               config (see "WHY THE DEFAULTS MATTER" below)
    Design/krnl_gemv_sparse.v  the Vitis RTL kernel top, same channel count
    gen_xo_<tag>.tcl           RTL Kernel Wizard config: NUM_AXIS + the table
    sparse_hbm_<tag>.cfg       nk / sp= / stream_connect for N channels
    slr_floorplan_<tag>.cfg    gemv -> SLR1, every mover -> SLR0
    host_defs_<tag>.h          the four constants host_sparse.cpp already uses
    BUILD.md                   the step-by-step for this one config

WHAT IS *NOT* GENERATED, BECAUSE IT DOES NOT CHANGE. Established by reading the
8x8 build chain end to end:

  * krnl_mm2s.cpp / krnl_s2mm.cpp -- a mover moves one 256-bit stream and does
    not know or care whether it is a weight, an index or an activation. The
    8x8 build already reused the DENSE movers unchanged. Only the COUNT moves.
  * All 7 IP cores (Multiplier, Adder, Accumulator, axis_data_fifo_pc/_v/_c,
    fifo_gen_vector_cycle). Every one is either per-block (the arithmetic) or
    per-PC at a fixed 256-bit width. fifo_gen_vector_cycle is 513 bits =
    A_PCS*256+1 and A_PCS is 2 at every T, because the activation window is 32
    elements by definition. So gen_xo phase 4 is byte-identical everywhere.
  * top_module_2N.vhd and the whole engine below it -- already fully generic.
    That is what the OOC sweep proved.

WHY THE GENERIC DEFAULTS MATTER. krnl_gemv_sparse.v is VERILOG instantiating a
VHDL entity. Verilog cannot pass VHDL generics, so two2N_axis MUST carry this
config's values as its ENTITY DEFAULTS or the kernel silently builds an 8x8
engine with the wrong number of ports wired to it. This is the single most
dangerous field in the whole generator and the reason each config gets its own
copy of the file rather than a shared one with an override.

REGRESSION CHECK, FREE: 8x8 is generated too, even though its build already
exists and ran bit-exact on hardware. Its generated files should describe the
SAME hardware as the hand-written ones. Diff them (BUILD.md says how) -- if the
generator is right there, it is right everywhere else.
"""

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # repository root; this file is in family_builds/
OUTROOT = HERE

EL_SIZE, A_IDX, IND_NUM, PC_WIDTH = 16, 32, 10, 256

# tag, cores, blocks, target kernel clock (MHz) -- see BUILD.md for how the
# targets were derived from the OOC sweep.
CONFIGS = [
    # tag     C   B   ooc_fmax  target
    ("4x4",    4,  4,   619.6,   400),
    ("8x4",    8,  4,   559.9,   375),
    ("16x3",  16,  3,   501.8,   350),
    ("8x8",    8,  8,   448.6,   325),
    ("4x24",   4, 24,   407.3,   300),
    ("4x32",   4, 32,   355.4,   250),
]


def ceildiv(a, b):
    return -(-a // b)


class Cfg(object):
    def __init__(self, tag, C, B, ooc, target):
        self.tag, self.C, self.B = tag, C, B
        self.ooc, self.target = ooc, target
        self.T = C * B
        self.W_IDX = 2 * B
        self.W_PCS = ceildiv(self.T * 32, PC_WIDTH)
        self.IND_BITS = IND_NUM * self.T
        # +2 for the sparsity code, which rides ABOVE the index bits at
        # ind_concat(IND_BITS+1 downto IND_BITS). Omitting it is what made
        # T=128 fail synthesis with "array index 1281 out of range".
        self.IND_PCS = ceildiv(self.IND_BITS + 2, PC_WIDTH)
        self.A_PCS = 2
        self.C_PCS = ceildiv(self.T * EL_SIZE, PC_WIDTH)
        self.total = self.W_PCS + self.IND_PCS + self.A_PCS + self.C_PCS
        self.dsps = 8 * self.T
        # Engine BRAM for the SLR0 budget note. MEASURED for 4x4 (31, from this
        # study's own routed report) and 8x8 (74, from the 300 MHz build); the rest
        # are the OOC sweep's ramb_prim x1.2. The ratio is NOT constant (4x4 was
        # 23->31 = 1.35x, 8x8 66->74 = 1.12x), so treat the estimates as rough.
        self.ramb_est = {'4x4': 31.0, '8x4': 44.4, '16x3': 62.4,
                         '8x8': 74.0, '4x24': 114.0, '4x32': 150.0}[tag]

    # ---- channel naming, used identically by every emitter ----------------
    def slaves(self):
        """[(port_prefix, cu_name, hbm_index)] for every input channel."""
        out, h = [], 0
        for i in range(self.W_PCS):
            out.append(("s_axis_w%d" % i, "mm2s_w%d" % i, h)); h += 1
        for i in range(self.IND_PCS):
            out.append(("s_axis_ind%d" % i, "mm2s_i%d" % i, h)); h += 1
        for i in range(self.A_PCS):
            out.append(("s_axis_a%d" % i, "mm2s_a%d" % i, h)); h += 1
        return out

    def masters(self):
        base = self.W_PCS + self.IND_PCS + self.A_PCS
        return [("m_axis_c%d" % i, "s2mm_c%d" % i, base + i)
                for i in range(self.C_PCS)]


# ===========================================================================
# two2N_axis.vhd
# ===========================================================================
VHD_HEAD = '''library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
--
-- two2N_axis -- AXI4-Stream splitter wrapper around two2N (the 2:M sparse engine).
--
-- GENERATED by make_family_build.py for the {tag} configuration
-- ({C} cores x {B} blocks = {T} blocks, {dsps} DSPs, {total} HBM pseudo-channels).
-- Do not hand-edit: regenerate instead, or the generics and the port list drift
-- apart and the mismatch is silent.
--
-- WHY THIS EXISTS (INTEGRATION_PLAN.md 0.2b):
-- two2N declares its streams BUNDLED -- s_axis_w_tdata is one {wbits}-bit port with
-- a {W_PCS}-bit tvalid/tready/tlast vector, and so on. The Vitis RTL Kernel Wizard
-- needs {total} SEPARATE, properly named AXIS interfaces so the packager can infer
-- them. THIS FILE CONTAINS NO LOGIC -- pure re-wiring:
--   1. slice tdata little-endian  -- PC k = tdata(256k+255 downto 256k)
--   2. slice the tvalid/tready/tlast vectors to scalars -- bit k = PC k
--   3. drive every master tkeep all-1s; ignore every slave tkeep
--      (UG1393: TKEEP must be all 1s when TLAST is 0 and may never be all
--       zeros; this engine has no ragged tails -- every beat is a full 256-bit
--       PC word)
--   4. ACCEPT AND DISCARD the index TLAST -- see the note below
--
-- ⚠️ THE GENERIC DEFAULTS BELOW ARE LOAD-BEARING. krnl_gemv_sparse.v is Verilog
-- instantiating this VHDL entity, and Verilog cannot pass VHDL generics. These
-- defaults ARE the configuration. Change them and nothing warns you.
--
-- INDEX TLAST. two2N has s_axis_ind_tvalid / _tready / _tdata but deliberately
-- NO s_axis_ind_tlast: the index stream carries no end-of-packet meaning for the
-- engine (weight TLAST marks end-of-calculation, activation TLAST marks
-- end-of-vector). The AXIS rules still require the signal to exist on the
-- interface, and krnl_mm2s emits one on its last beat like every other channel.
-- So the wrapper DECLARES the {IND_PCS} index tlast input(s) and leaves them
-- unconnected. Nothing is fabricated; the marker is simply not meaningful here.
--
-- ASSIGNMENT STYLE differs from the hand-written 8x8 file: this generator emits
-- ELEMENT-WISE slice assignments where that file used one descending
-- concatenation. The two are exactly equivalent in VHDL, and element-wise has no
-- special case at N=1 (T=16 has a single index PC and a single output PC).
--
-- The verified two2N netlist below is untouched, so an OOC synthesis of this
-- wrapper must report utilisation and WNS IDENTICAL to the bare two2N numbers.
-- A wrapper that costs a LUT is a wrapper with logic in it.
-- ----------------------------------------------------------------------------

entity two2N_axis is
    generic(
        EL_SIZE     : integer := {EL_SIZE};    -- Bit size of each element
        W_IDX       : integer := {W_IDX};    -- matrix elements per core = 2 x BLOCKS_NUM
        A_IDX       : integer := {A_IDX};    -- vector elements (the window)
        IND_NUM     : integer := {IND_NUM};    -- index bits per block
        BLOCKS_NUM  : integer := {B};    -- C blocks per core (2 elements each)
        CORES_NUM   : integer := {C};    -- C Cores

        PC_WIDTH    : integer := {PC_WIDTH};   -- HBM pseudo-channel data width
        W_PCS       : integer := {W_PCS};    -- weight PCs ({W_PCS}*256 = {wbits} bits)
        A_PCS       : integer := {A_PCS};    -- activation PCs
        IND_PCS     : integer := {IND_PCS};    -- index PCs ({IND_PCS}*256 = {ibits}, {IND_BITS} used)
        C_PCS       : integer := {C_PCS};    -- output PCs ({C_PCS}*256 = {cbits} bits)
        IND_BITS    : integer := {IND_BITS}    -- useful index bits (remainder is padding)
    );
    port(
        ap_clk   : in std_logic;
        ap_rst_n : in std_logic;
'''


def vhd_slave_ports(names, comment):
    L = ["", "        -- ---- %s ----" % comment]
    for n in names:
        L += ["        %s_tdata  : in  std_logic_vector(PC_WIDTH-1 downto 0);" % n,
              "        %s_tkeep  : in  std_logic_vector(PC_WIDTH/8-1 downto 0);" % n,
              "        %s_tvalid : in  std_logic;" % n,
              "        %s_tready : out std_logic;" % n,
              "        %s_tlast  : in  std_logic;" % n,
              ""]
    return L[:-1]


def gen_vhd(c):
    s = VHD_HEAD.format(
        tag=c.tag, C=c.C, B=c.B, T=c.T, dsps=c.dsps, total=c.total,
        EL_SIZE=EL_SIZE, A_IDX=A_IDX, IND_NUM=IND_NUM, PC_WIDTH=PC_WIDTH,
        W_IDX=c.W_IDX, W_PCS=c.W_PCS, A_PCS=c.A_PCS, IND_PCS=c.IND_PCS,
        C_PCS=c.C_PCS, IND_BITS=c.IND_BITS,
        wbits=c.W_PCS * 256, ibits=c.IND_PCS * 256, cbits=c.C_PCS * 256)
    L = [s.rstrip("\n")]

    w = ["s_axis_w%d" % i for i in range(c.W_PCS)]
    d = ["s_axis_ind%d" % i for i in range(c.IND_PCS)]
    a = ["s_axis_a%d" % i for i in range(c.A_PCS)]
    m = ["m_axis_c%d" % i for i in range(c.C_PCS)]

    L += vhd_slave_ports(w, "Weights : %d slave AXIS channels" % c.W_PCS)
    L += ["", "        -- ---- Indices : %d slave AXIS channel(s) ----" % c.IND_PCS,
          "        -- tlast is declared because the AXIS rules require it and krnl_mm2s",
          "        -- drives it; the engine has no such port, so it is left unconnected."]
    for n in d:
        L += ["        %s_tdata  : in  std_logic_vector(PC_WIDTH-1 downto 0);" % n,
              "        %s_tkeep  : in  std_logic_vector(PC_WIDTH/8-1 downto 0);" % n,
              "        %s_tvalid : in  std_logic;" % n,
              "        %s_tready : out std_logic;" % n,
              "        %s_tlast  : in  std_logic;" % n, ""]
    L = L[:-1]
    L += vhd_slave_ports(a, "Activations : %d slave AXIS channels" % c.A_PCS)

    L += ["", "        -- ---- Output : %d master AXIS channel(s) ----" % c.C_PCS]
    for i, n in enumerate(m):
        last = (i == len(m) - 1)
        L += ["        %s_tdata  : out std_logic_vector(PC_WIDTH-1 downto 0);" % n,
              "        %s_tkeep  : out std_logic_vector(PC_WIDTH/8-1 downto 0);" % n,
              "        %s_tvalid : out std_logic;" % n,
              "        %s_tready : in  std_logic;" % n,
              "        %s_tlast  : out std_logic%s" % (n, "" if last else ";"), ""]
    L = L[:-1]
    L += ["    );", "end entity two2N_axis;", "", "",
          "architecture rewire of two2N_axis is", "",
          "    -- bundled buses, exactly as two2N declares them",
          "    signal w_tdata  : std_logic_vector(W_PCS*PC_WIDTH-1 downto 0);",
          "    signal w_tvalid : std_logic_vector(W_PCS-1 downto 0);",
          "    signal w_tready : std_logic_vector(W_PCS-1 downto 0);",
          "    signal w_tlast  : std_logic_vector(W_PCS-1 downto 0);", "",
          "    signal i_tdata  : std_logic_vector(IND_PCS*PC_WIDTH-1 downto 0);",
          "    signal i_tvalid : std_logic_vector(IND_PCS-1 downto 0);",
          "    signal i_tready : std_logic_vector(IND_PCS-1 downto 0);", "",
          "    signal a_tdata  : std_logic_vector(A_PCS*PC_WIDTH-1 downto 0);",
          "    signal a_tvalid : std_logic_vector(A_PCS-1 downto 0);",
          "    signal a_tready : std_logic_vector(A_PCS-1 downto 0);",
          "    signal a_tlast  : std_logic_vector(A_PCS-1 downto 0);", "",
          "    signal c_tdata  : std_logic_vector((BLOCKS_NUM*CORES_NUM*EL_SIZE)-1 downto 0);",
          "    signal c_tvalid : std_logic_vector(C_PCS-1 downto 0);",
          "    signal c_tready : std_logic_vector(C_PCS-1 downto 0);",
          "    signal c_tlast  : std_logic_vector(C_PCS-1 downto 0);", "",
          "    constant KEEP_ALL : std_logic_vector(PC_WIDTH/8-1 downto 0) := (others => '1');",
          "", "begin", "",
          "    -- =======================================================================",
          "    -- 1. JOIN the per-channel slave ports into the bundled buses.",
          "    --    Little-endian: PC 0 occupies the LSBs, matching HBM/AXI byte order",
          "    --    and the convention every other module in this design follows.",
          "    --    The 2-bit Sparsity code rides in the LAST index PC's padding, bits",
          "    --    [%d:%d] of the joined index word -- it is buffer CONTENT written by"
          % (c.IND_BITS + 1, c.IND_BITS),
          "    --    the host packer and needs nothing from this wrapper.",
          "    -- ======================================================================="]

    def sl(i):
        return "((%d+1)*PC_WIDTH)-1 downto %d*PC_WIDTH" % (i, i)

    for i in range(c.W_PCS):
        L.append("    w_tdata(%s) <= s_axis_w%d_tdata;" % (sl(i), i))
    L.append("")
    for i in range(c.W_PCS):
        L.append("    w_tvalid(%d) <= s_axis_w%d_tvalid;" % (i, i))
    L.append("")
    for i in range(c.W_PCS):
        L.append("    w_tlast(%d)  <= s_axis_w%d_tlast;" % (i, i))
    L.append("")
    for i in range(c.IND_PCS):
        L.append("    i_tdata(%s) <= s_axis_ind%d_tdata;" % (sl(i), i))
    L.append("")
    for i in range(c.IND_PCS):
        L.append("    i_tvalid(%d) <= s_axis_ind%d_tvalid;" % (i, i))
    L += ["    -- s_axis_ind*_tlast: intentionally unconnected -- two2N has no index tlast port.",
          ""]
    for i in range(c.A_PCS):
        L.append("    a_tdata(%s) <= s_axis_a%d_tdata;" % (sl(i), i))
    L.append("")
    for i in range(c.A_PCS):
        L.append("    a_tvalid(%d) <= s_axis_a%d_tvalid;" % (i, i))
    L.append("")
    for i in range(c.A_PCS):
        L.append("    a_tlast(%d)  <= s_axis_a%d_tlast;" % (i, i))
    L += ["",
          "    -- slave tkeep is deliberately unused: every beat is a full PC word, so the",
          "    -- engine has no use for byte enables. Reading them would be the only way",
          "    -- this wrapper could acquire logic.", "",
          "    -- =======================================================================",
          "    -- 2. FAN OUT the bundled tready back to the per-channel slave ports.",
          "    -- ======================================================================="]
    for i in range(c.W_PCS):
        L.append("    s_axis_w%d_tready <= w_tready(%d);" % (i, i))
    L.append("")
    for i in range(c.IND_PCS):
        L.append("    s_axis_ind%d_tready <= i_tready(%d);" % (i, i))
    L.append("")
    for i in range(c.A_PCS):
        L.append("    s_axis_a%d_tready <= a_tready(%d);" % (i, i))
    L += ["",
          "    -- =======================================================================",
          "    -- 3. SPLIT the bundled output bus into the %d master channel(s), and drive"
          % c.C_PCS,
          "    --    tkeep all-1s (UG1393 requires it; no ragged tails exist here).",
          "    -- ======================================================================="]
    for i in range(c.C_PCS):
        L.append("    m_axis_c%d_tdata  <= c_tdata(%s);" % (i, sl(i)))
    L.append("")
    for i in range(c.C_PCS):
        L.append("    m_axis_c%d_tvalid <= c_tvalid(%d);" % (i, i))
    L.append("")
    for i in range(c.C_PCS):
        L.append("    m_axis_c%d_tlast  <= c_tlast(%d);" % (i, i))
    L.append("")
    for i in range(c.C_PCS):
        L.append("    m_axis_c%d_tkeep  <= KEEP_ALL;" % i)
    L.append("")
    for i in range(c.C_PCS):
        L.append("    c_tready(%d) <= m_axis_c%d_tready;" % (i, i))
    L += ["",
          "    -- =======================================================================",
          "    -- 4. The verified engine, untouched.",
          "    -- =======================================================================",
          "    DUT : entity work.two2N",
          "        generic map (",
          "            EL_SIZE    => EL_SIZE,",
          "            W_IDX      => W_IDX,",
          "            A_IDX      => A_IDX,",
          "            IND_NUM    => IND_NUM,",
          "            BLOCKS_NUM => BLOCKS_NUM,",
          "            CORES_NUM  => CORES_NUM,",
          "            PC_WIDTH   => PC_WIDTH,",
          "            W_PCS      => W_PCS,",
          "            A_PCS      => A_PCS,",
          "            IND_PCS    => IND_PCS,",
          "            C_PCS      => C_PCS,",
          "            IND_BITS   => IND_BITS",
          "        )",
          "        port map (",
          "            clk               => ap_clk,",
          "            resetn            => ap_rst_n,", "",
          "            s_axis_w_tdata    => w_tdata,",
          "            s_axis_w_tvalid   => w_tvalid,",
          "            s_axis_w_tready   => w_tready,",
          "            s_axis_w_tlast    => w_tlast,", "",
          "            s_axis_ind_tdata  => i_tdata,",
          "            s_axis_ind_tvalid => i_tvalid,",
          "            s_axis_ind_tready => i_tready,", "",
          "            s_axis_a_tdata    => a_tdata,",
          "            s_axis_a_tvalid   => a_tvalid,",
          "            s_axis_a_tlast    => a_tlast,",
          "            s_axis_a_tready   => a_tready,", "",
          "            m_axis_c_tdata    => c_tdata,",
          "            m_axis_c_tvalid   => c_tvalid,",
          "            m_axis_c_tlast    => c_tlast,",
          "            m_axis_c_tready   => c_tready",
          "        );", "", "end architecture rewire;", ""]
    return "\n".join(L)


# ===========================================================================
# krnl_gemv_sparse.v
# ===========================================================================
def gen_verilog(c):
    sl, ms = c.slaves(), c.masters()
    L = ["// ---------------------------------------------------------------------------",
         "// krnl_gemv_sparse.v -- Vitis RTL kernel top for the 2:M SPARSE GEMV engine.",
         "//",
         "// GENERATED by make_family_build.py for the %s configuration" % c.tag,
         "// (%d cores x %d blocks = %d blocks, %d DSPs, %d AXI4-Stream ports)."
         % (c.C, c.B, c.T, c.dsps, c.total),
         "//",
         "// This is the RTL Kernel Wizard's generated top with ONE change, exactly where",
         "// the generated comments invite it: the placeholder example instance is",
         "// replaced by two2N_axis (the AXIS splitter around the verified two2N).",
         "// The module name, parameters and ports follow the wizard's conventions --",
         "// package_xo infers the %d AXI4-Stream interfaces from them." % c.total,
         "//",
         "// INDEX TLAST. two2N has no index tlast port and needs none: weight TLAST is",
         "// the end-of-calculation marker and activation TLAST the end-of-vector one.",
         "// The AXIS rules still require the signal on the interface, and krnl_mm2s",
         "// drives it, so the wrapper accepts all %d and leaves them unconnected." % c.IND_PCS,
         "//",
         "// SPARSITY is NOT a port. The 2-bit code rides in the LAST index PC's padding,",
         "// bits [%d:%d] of the joined %d-bit index word. It is buffer CONTENT written"
         % (c.IND_BITS + 1, c.IND_BITS, c.IND_PCS * 256),
         "// by the host packer, invisible to this kernel and to every data mover.",
         "//",
         "// Mixed-language: this Verilog top instantiates a VHDL entity. Vivado resolves",
         "// that by entity name; two2N_axis's generics are DEFAULTED to this config's",
         "// values, because no generic mapping is possible from Verilog.",
         "// ---------------------------------------------------------------------------",
         "", "`default_nettype none", "`timescale 1 ns / 1 ps",
         "// Top level of the kernel. Do not modify module name, parameters or ports.",
         "module krnl_gemv_sparse #("]
    par = ["  parameter integer C_%s_TDATA_WIDTH = 256" % n.upper()
           for n, _, _ in sl + ms]
    L.append(",\n".join(par))
    L += [")", "(", "  // System Signals",
          "  input  wire                                  ap_clk          ,",
          "  input  wire                                  ap_rst_n        ,"]
    body = []
    for n, _, _ in sl:
        u = "C_%s_TDATA_WIDTH" % n.upper()
        body.append("\n".join([
            "  // AXI4-Stream (slave) interface %s" % n,
            "  input  wire                                  %s_tvalid," % n,
            "  output wire                                  %s_tready," % n,
            "  input  wire [%s-1:0]   %s_tdata ," % (u, n),
            "  input  wire [%s/8-1:0] %s_tkeep ," % (u, n),
            "  input  wire                                  %s_tlast ," % n]))
    for n, _, _ in ms:
        u = "C_%s_TDATA_WIDTH" % n.upper()
        body.append("\n".join([
            "  // AXI4-Stream (master) interface %s" % n,
            "  output wire                                  %s_tvalid," % n,
            "  input  wire                                  %s_tready," % n,
            "  output wire [%s-1:0]   %s_tdata ," % (u, n),
            "  output wire [%s/8-1:0] %s_tkeep ," % (u, n),
            "  output wire                                  %s_tlast ," % n]))
    txt = "\n".join(body)
    L.append(txt[:txt.rfind(",")] + txt[txt.rfind(",") + 1:])
    L += [");", "",
          "///////////////////////////////////////////////////////////////////////////////",
          "// Begin control interface RTL.  Modifying not recommended.",
          "///////////////////////////////////////////////////////////////////////////////",
          "// (none -- this is an ap_ctrl_none free-running kernel: no s_axi_control, no",
          "//  ap_start/ap_done. It runs from the moment the xclbin is programmed and",
          "//  interacts with the rest of the system only through the streams above.)",
          "",
          "///////////////////////////////////////////////////////////////////////////////",
          "// Kernel logic.",
          "///////////////////////////////////////////////////////////////////////////////",
          "two2N_axis inst_gemv (",
          "  .ap_clk            ( ap_clk            ),",
          "  .ap_rst_n          ( ap_rst_n          ),", ""]
    conn = []
    for n, _, _ in sl + ms:
        conn.append("\n".join([
            "  .%s_tvalid ( %s_tvalid )," % (n, n),
            "  .%s_tready ( %s_tready )," % (n, n),
            "  .%s_tdata  ( %s_tdata  )," % (n, n),
            "  .%s_tkeep  ( %s_tkeep  )," % (n, n),
            "  .%s_tlast  ( %s_tlast  )," % (n, n)]))
    ct = "\n\n".join(conn)
    L.append(ct[:ct.rfind(",")] + ct[ct.rfind(",") + 1:])
    L += [");", "", "endmodule", "`default_nettype wire", ""]
    return "\n".join(L)


# ===========================================================================
# gen_xo_<tag>.tcl -- only the wizard block differs from gen_xo_sparse.tcl
# ===========================================================================
def gen_xo_tcl(c):
    src_tpl = os.path.join(ROOT, "Vitis", "gen_xo_sparse.tcl")
    base = io.open(src_tpl, encoding="utf-8").read()

    rows, i = [], 0
    for n, _, _ in c.slaves():
        rows.append("        %02d {%-12s read_only}" % (i, n)); i += 1
    for n, _, _ in c.masters():
        rows.append("        %02d {%-12s write_only}" % (i, n)); i += 1
    table = "\n".join(rows)

    # 1. stream count
    base = base.replace("CONFIG.NUM_AXIS       {17}",
                        "CONFIG.NUM_AXIS       {%d}" % c.total)
    # 2. the stream table -- replace everything between "set streams {" and its "}"
    a = base.index("    set streams {")
    b = base.index("\n    }\n", a)
    base = base[:a] + "    set streams {\n" + table + base[b:]
    # 3. per-config build dir + .xo name, so six builds never share a tree.
    #
    # ⚠️ SET UNCONDITIONALLY. The original gen_xo_sparse.tcl guards this with
    # `if {![info exists ::GEN_XO_TAG]}` because there the tag is an OPT-IN
    # override you set before sourcing. Here the tag IS the configuration's
    # identity, and ::GEN_XO_TAG is a global that SURVIVES ACROSS `source`
    # CALLS IN ONE VIVADO SESSION. Keeping the guard meant: source
    # gen_xo_8x4.tcl (sets _8x4), then source gen_xo_16x3.tcl in the same
    # session -> the guard sees the tag already set, keeps _8x4, and writes the
    # 16x3 DESIGN into krnl_gemv_sparse_8x4.xo. Silent: no error, no warning,
    # and the only symptom is a missing xo_build_<tag>/ directory.
    # Cost one build and corrupted an already-verified .xo before it was found.
    base = base.replace('if {![info exists ::GEN_XO_TAG]} { set ::GEN_XO_TAG "" }',
                        '# GENERATED: unconditional -- this tag is the config\'s identity, and\n'
                        '# ::GEN_XO_TAG persists across `source` calls in one Vivado session.\n'
                        'set ::GEN_XO_TAG "_%s"' % c.tag)
    # Same hazard for the phase list: a retry that sets ::GEN_XO_PHASES {5 6}
    # leaves it set, and every later source silently runs only those phases.
    base = base.replace('if {![info exists ::GEN_XO_PHASES]} { set ::GEN_XO_PHASES {1 2 3 4 5 6} }',
                        '# GENERATED: unconditional, same session-persistence hazard as the tag.\n'
                        '# To re-run only some phases, set ::GEN_XO_PHASES AFTER sourcing is not\n'
                        '# possible -- edit this line, or source the stock gen_xo_sparse.tcl.\n'
                        'set ::GEN_XO_PHASES {1 2 3 4 5 6}')
    # 4. this config's own two2N_axis / krnl_gemv_sparse, not the 8x8 ones
    base = base.replace('set src    "$repo/GEMV_4.0_Source"',
                        'set src    "$repo/GEMV_4.0_Source"\n'
                        '# GENERATED for %s: the two config-specific files come from the\n'
                        '# family build dir; everything else is the shared verified engine.\n'
                        'set gsrc   "$repo/family_builds/%s"' % (c.tag, c.tag))
    # Both spellings. Phase 4 names two2N_axis.vhd literally, but PHASE 3 writes
    # the kernel top as "$src/Design/${kname}.v" -- a Tcl variable, not the
    # literal filename. Rewriting only the literal left phase 3 copying the 8x8
    # Verilog top into every config: the wrapper would have been right and the
    # kernel top wrong. Caught by grepping the generated file for surviving
    # $src references; keep that check in the verification step below.
    for f in ("krnl_gemv_sparse.v", "two2N_axis.vhd", "${kname}.v"):
        base = base.replace('"$src/Design/%s"' % f, '"$gsrc/Design/%s"' % f)
    base = base.replace('        Design/krnl_gemv_sparse.v\n    Design/two2N_axis.vhd\n', '')
    base = base.replace("""    Design/krnl_gemv_sparse.v
    Design/two2N_axis.vhd
""", "")

    # ⚠️ AND PUT THEM BACK, CHECKED UNDER $gsrc. Removing them from the shared
    # pre-flight list left the TWO MOST LIKELY FILES TO BE MISSING as the only
    # two unchecked -- they live in the per-config directory that has to be
    # scp'd separately, unlike the 10 shared sources. The symptom without this
    # is a phase-3 Tcl stack trace ("error copying ... no such file"), three
    # phases and several minutes in, instead of a one-line message up front.
    # That is exactly what gen_xo_sparse.tcl's pre-flight exists to prevent:
    # "Cheaper to fail here than three phases in."
    base = base.replace(
        'say "pre-flight ok: all [llength $need_files] source files present"',
        'set gmissing {}\n'
        'foreach f {Design/krnl_gemv_sparse.v Design/two2N_axis.vhd} {\n'
        '    if {![file exists "$gsrc/$f"]} { lappend gmissing $f }\n'
        '}\n'
        'if {[llength $gmissing]} {\n'
        '    say "ERROR: [llength $gmissing] per-config file(s) missing under $gsrc :"\n'
        '    foreach f $gmissing { say "         $f" }\n'
        '    say "       copy the whole directory up, e.g."\n'
        '    say "         scp -r family_builds/%s <server>:$repo/family_builds/"\n'
        '    return\n'
        '}\n'
        'say "pre-flight ok: [llength $need_files] shared + 2 per-config source files present"'
        % c.tag)
    hdr = ("# GENERATED by make_family_build.py for %s (%dx%d, %d channels).\n"
           "# Differs from gen_xo_sparse.tcl in exactly four places: NUM_AXIS, the\n"
           "# stream table, GEN_XO_TAG, and the path to the two config-specific files.\n"
           % (c.tag, c.C, c.B, c.total))
    return hdr + base


# ===========================================================================
# sparse_hbm_<tag>.cfg  and  slr_floorplan_<tag>.cfg
# ===========================================================================
def gen_link_cfg(c):
    sl, ms = c.slaves(), c.masters()
    movers = [cu for _, cu, _ in sl]
    L = ["# " + "-" * 74,
         "# sparse_hbm_%s.cfg -- link configuration for the %s configuration." % (c.tag, c.tag),
         "#",
         "#   %d x krnl_mm2s  ->  krnl_gemv_sparse (free-running)  ->  %d x krnl_s2mm"
         % (len(sl), len(ms)),
         "#",
         "# GENERATED by make_family_build.py. %d cores x %d blocks = %d C Blocks,"
         % (c.C, c.B, c.T),
         "# %d DSPs, %d MACs/cycle, %d of the U280's 32 HBM pseudo-channels"
         % (c.dsps, 2 * c.T, c.total),
         "# (%d weights + %d indices + %d activations in, %d results out; %d spare)."
         % (c.W_PCS, c.IND_PCS, c.A_PCS, c.C_PCS, 32 - c.total),
         "#",
         "# SAME MOVER KERNELS AS EVERY OTHER CONFIG. krnl_mm2s / krnl_s2mm are",
         "# unchanged and are not rebuilt -- an index PC is just another 256-bit stream",
         "# to them. Only the COUNT changes.",
         "#",
         "# THE SPARSITY CODE IS NOT A STREAM. The 2-bit code rides in the LAST index",
         "# PC's padding, bits [%d:%d] of the joined %d-bit index word (= %s bits"
         % (c.IND_BITS + 1, c.IND_BITS, c.IND_PCS * 256,
            "s_axis_ind%d" % (c.IND_PCS - 1)),
         "# [%d:%d]). It is buffer CONTENT written by the Python packer; no mover, no"
         % (c.IND_BITS + 1 - (c.IND_PCS - 1) * 256, c.IND_BITS - (c.IND_PCS - 1) * 256),
         "# link line and no host argument knows about it.",
         "#",
         "# Used as:  v++ -l --config sparse_hbm_%s.cfg --config impl_family.cfg \\" % c.tag,
         "#                  --config slr_floorplan_%s.cfg --kernel_frequency %d ..."
         % (c.tag, c.target),
         "# " + "-" * 74, "", "[connectivity]", "",
         "# ---- compute units ---------------------------------------------------------",
         "# Names are given explicitly. Without them every sp= and stream_connect= below",
         "# would address krnl_mm2s_1 .. krnl_mm2s_%d by position, and one transposed" % len(sl),
         "# digit would silently bind an index channel to a weight stream -- wrong",
         "# answers, no error.",
         "nk=krnl_mm2s:%d:%s" % (len(sl), ".".join(movers)),
         "nk=krnl_gemv_sparse:1:gemv",
         "nk=krnl_s2mm:%d:%s" % (len(ms), ".".join(cu for _, cu, _ in ms)), "",
         "# ---- memory binding: one m_axi argument per pseudo-channel ------------------",
         "# 'in' / 'out' are the kernel ARGUMENT names, not the bundle names."]
    prev = None
    for n, cu, h in sl:
        kind = cu.split("_")[1][0]
        if prev and kind != prev:
            L.append("")
        L.append("sp=%s.in:HBM[%d]" % (cu, h))
        prev = kind
    L.append("")
    for n, cu, h in ms:
        L.append("sp=%s.out:HBM[%d]" % (cu, h))
    L += ["",
          "# ---- kernel-to-kernel streams ----------------------------------------------",
          "# Format: <producer_cu>.<out_port>:<consumer_cu>.<in_port>[:<fifo_depth>]",
          "#",
          "# WARNING: a mistyped port name here is a link WARNING, not an error. The",
          "# stream is simply left dangling and the engine's barrier join waits forever",
          "# for a channel that never arrives. If hw_emu hangs, check these first -- the",
          "# join waits on ALL %d weight+index PCs, so a single dangling index stream"
          % (c.W_PCS + c.IND_PCS),
          "# hangs the whole engine with no other symptom.",
          "#",
          "# The port names on the gemv side are two2N_axis's entity ports -- s_axis_ind0",
          "# not s_axis_i0. The mover CU names are abbreviated (mm2s_i0); the kernel port",
          "# names are not. Do not \"tidy\" one to match the other."]
    prev = None
    for n, cu, h in sl:
        kind = cu.split("_")[1][0]
        if prev and kind != prev:
            L.append("")
        L.append("stream_connect=%s.out:gemv.%s" % (cu, n))
        prev = kind
    L.append("")
    for n, cu, h in ms:
        L.append("stream_connect=gemv.%s:%s.in" % (n, cu))
    L += ["",
          "# ---- kernel clock ----------------------------------------------------------",
          "# NOT SET HERE. A [clock] section maps to v++ --clock, which this platform",
          "# rejects (xilinx_u280_xdma_201920_3 has no fixed reference clocks). Use",
          "#     v++ -l ... --kernel_frequency %d" % c.target,
          "# OOC Fmax for this config was %.1f MHz on the bare engine at 1.5 ns; the" % c.ooc,
          "# in-system target above de-rates that by the ratio MEASURED on 8x8",
          "# (325 in-system / 448.6 OOC = 0.724), rounded down to a round number.",
          ""]
    return "\n".join(L)


def gen_slr_cfg_slr0(c):
    """DEFAULT: every CU in SLR0. Zero SLL crossings.

    THE POLICY: keep the design in ONE die, and split only when a build fails.
    One rule, six applications -- not a threshold guessed from one data point.
    """
    sl, ms = c.slaves(), c.masters()
    n_mov = len(sl) + len(ms)
    # BRAM budget, from the MEASURED 4x4 build: mm2s = 14 BRAM, s2mm = 16,
    # platform ~204 in SLR0, SLR0 total 720.
    mov_bram = len(sl) * 14 + len(ms) * 16
    est = mov_bram + int(round(c.ramb_est)) + 204
    pct = 100.0 * est / 720.0
    L = ["# " + "-" * 74,
         "# slr_floorplan_%s.cfg -- every compute unit in SLR0." % c.tag,
         "#",
         "# GENERATED by make_family_build.py. THE DEFAULT FLOORPLAN for every",
         "# configuration in the family study.",
         "#",
         "# WHY SLR0-ONLY IS THE DEFAULT. Measured on the 4x4 build at 400 MHz with the",
         "# SPLIT floorplan (gemv->SLR1, movers->SLR0): WNS -0.048, and the worst path was",
         "#",
         "#   mm2s_i0/.../regslice_both_out -> gemv/.../IND_FIFOS[0]/.../mem_reg_0",
         "#   Data Path Delay 1.842ns   logic 0.179ns (9.7%)   route 1.663ns (90.3%)",
         "#   Logic Levels 1 (LUT3)     Inter-SLR Compensation 0.222ns",
         "#",
         "# ONE LUT3, 90%% of its delay on wire, and the SLR-crossing penalty alone was",
         "# 4.6x the violation. The split was costing far more than it bought.",
         "#",
         "# The split floorplan was designed for 8x8 -- a 66,186-LUT engine and 17 CUs that",
         "# needed a die each. At 4x4 the WHOLE design is ~28,000 LUT, 2.4%% of the device.",
         "#",
         "# THE RULE: single die until a build fails, then split. slr_floorplan_%s_split.cfg" % c.tag,
         "# is the fallback, kept alongside so switching is one --config change.",
         "#",
         "# ---- BRAM BUDGET FOR THIS CONFIG -------------------------------------------",
         "#   %2d mm2s x 14 + %d s2mm x 16 = %3d BRAM (movers)"
         % (len(sl), len(ms), mov_bram),
         "#   engine %s%3d BRAM" % ("(measured) " if c.tag in ("4x4", "8x8") else "(estimated) ",
                                    int(round(c.ramb_est))),
         "#   platform in SLR0        ~204 BRAM",
         "#   TOTAL ~%3d of SLR0's 720 = %.0f%%" % (est, pct)]
    if pct >= 95:
        L += ["#",
              "# ⚠️ THIS ONE IS PREDICTED NOT TO FIT. place_design should refuse with an",
              "# over-utilisation error naming SLR0, an hour or two in -- BEFORE timing gets",
              "# a say. That is a cheap, unambiguous signal: switch to the _split config and",
              "# rebuild. Kept as the default anyway so the policy is applied uniformly and",
              "# the crossover is MEASURED rather than assumed."]
    elif pct >= 75:
        L += ["#",
              "# ⚠️ TIGHT. If place_design reports SLR0 over-utilisation, switch to",
              "# slr_floorplan_%s_split.cfg and rebuild." % c.tag]
    L += ["# " + "-" * 74, "", "[connectivity]", "",
          "# ---- the compute engine, beside its movers: zero SLL crossings -------------",
          "slr=gemv:SLR0", ""]
    prev = None
    for _, cu, _ in sl:
        kind = cu.split("_")[1][0]
        if prev and kind != prev:
            L.append("")
        L.append("slr=%s:SLR0" % cu)
        prev = kind
    L.append("")
    for _, cu, _ in ms:
        L.append("slr=%s:SLR0" % cu)
    L.append("")
    return "\n".join(L)


def gen_slr_cfg(c):
    sl, ms = c.slaves(), c.masters()
    n_mov = len(sl) + len(ms)
    L = ["# " + "-" * 74,
         "# slr_floorplan_%s_split.cfg -- FALLBACK: gemv to its own die." % c.tag,
         "#",
         "# NOT THE DEFAULT. Use slr_floorplan_%s.cfg (everything in SLR0) first and" % c.tag,
         "# switch to this only when a build fails -- either place_design refuses on SLR0",
         "# over-utilisation, or the design places but misses its frequency.",
         "#",
         "# Measured counterexample at 4x4: this split MISSED 400 MHz by 48 ps, and the",
         "# critical path's inter-SLR compensation alone was 222 ps. A split costs real",
         "# time on every stream that crosses; it only pays when the engine genuinely",
         "# cannot share a die with the movers and the platform.",
         "#",
         "# GENERATED by make_family_build.py. THIRD config file, kept separate from",
         "# sparse_hbm_%s.cfg (what is connected to what) and impl_family.cfg (how hard" % c.tag,
         "# the tools try). One variable per file.",
         "#",
         "# MOVERS -> SLR0. They are the only kernels with an m_axi port, and all 32 HBM",
         "# pseudo-channels are in SLR0. They are also tiny (~1.5-2 kLUT each).",
         "#",
         "# GEMV -> SLR1. It has NO m_axi -- it speaks only AXI4-Stream -- so proximity",
         "# to HBM buys it nothing. What it needs is to be WHOLE INSIDE ONE DIE so the",
         "# %d-element activation broadcast never crosses silicon." % A_IDX,
         "#",
         "# SLR2 -> unused. Emptying the die furthest from HBM removes the largest",
         "# single source of crossings.",
         "#",
         "# The %d kernel-to-kernel links all cross SLR0<->SLR1. Every one is" % n_mov,
         "# AXI4-Stream -- a handshake with tready backpressure -- so an extra cycle or",
         "# two of crossing latency costs throughput nothing, and Vitis inserts the SLL",
         "# pipeline registers automatically.",
         "#"]
    if n_mov >= 24:
        L += ["# ⚠️ %d MOVERS IS A LOT FOR SLR0. At ~13.5 BRAM each that is ~%d BRAM on top"
              % (n_mov, int(round(n_mov * 13.5))),
              "# of the platform's 204, against SLR0's 720. If place_design fails with an",
              "# over-utilisation error naming SLR0, DELETE every slr=mm2s/s2mm line below",
              "# and keep only the gemv line -- the movers follow their HBM ports to SLR0",
              "# on their own, just without the hard pin.",
              "#"]
    L += ["# " + "-" * 74, "", "[connectivity]", "",
          "# ---- the compute engine: one die, to itself -------------------------------",
          "slr=gemv:SLR1", "",
          "# ---- the data movers: beside HBM ------------------------------------------"]
    prev = None
    for _, cu, _ in sl:
        kind = cu.split("_")[1][0]
        if prev and kind != prev:
            L.append("")
        L.append("slr=%s:SLR0" % cu)
        prev = kind
    L.append("")
    for _, cu, _ in ms:
        L.append("slr=%s:SLR0" % cu)
    L.append("")
    return "\n".join(L)


# ===========================================================================
# host_defs_<tag>.h
# ===========================================================================
def gen_host_defs(c):
    return "\n".join([
        "// GENERATED by make_family_build.py for %s (%d cores x %d blocks)."
        % (c.tag, c.C, c.B),
        "//",
        "// host_sparse.cpp already carries these as `static const int` at its top --",
        "// it was written parameterised, so NOTHING ELSE in it needs to change. Either",
        "// #include this file and delete those six lines, or just retype the six values.",
        "//",
        "// LANES is the one that is easy to get wrong: it is CORES x BLOCKS, and it",
        "// divides the output-beat count. Wrong LANES = wrong throughput arithmetic in",
        "// the summary, with correct hardware -- a silent reporting error.",
        "",
        "static const int PC_BYTES  = %d;   // 256-bit pseudo-channel beat" % (PC_WIDTH // 8),
        "static const int W_PCS     = %d;" % c.W_PCS,
        "static const int IND_PCS   = %d;" % c.IND_PCS,
        "static const int A_PCS     = %d;" % c.A_PCS,
        "static const int C_PCS     = %d;" % c.C_PCS,
        "static const int LANES     = %d;   // %d cores x %d blocks" % (c.T, c.C, c.B),
        "static const int WIN_ELEMS = %d;   // activation elements per window" % A_IDX,
        "",
        "// Where the 2-bit sparsity code sits. Derived, not hardcoded, so every other",
        "// configuration is a constants change and nothing else -- see the note below.",
        "static const int IND_BITS  = %d;   // 10 x LANES, index bits in the joined word"
        % c.IND_BITS,
        "static const int SP_PC     = IND_BITS / 256;         // = %d, which index PC"
        % (c.IND_BITS // 256),
        "static const int SP_BYTE   = (IND_BITS %% 256) / 8;   // = %d, byte within it"
        % ((c.IND_BITS % 256) // 8),
        "",
        "// ---- AND ONE THING THAT IS *NOT* A CONSTANT IN host_sparse.cpp ------------",
        "//",
        "// lap_sparsity() reads the 2-bit code back out of the index image with a",
        "// HARDCODED offset:",
        "//",
        "//     return (unsigned)(ind_pc2.p[k * PC_BYTES + 16] & 0x3);   // <- 8x8 ONLY",
        "//",
        "// That offset is 8x8-specific. The code rides at bit IND_BITS of the JOINED",
        "// index word, so both which PC holds it and the byte within that PC move with",
        "// the configuration. For %s (IND_BITS = %d) it is:" % (c.tag, c.IND_BITS),
        "//",
        "//     return (unsigned)(ind_pc%d.p[k * PC_BYTES + %d] & 0x3);"
        % (c.IND_BITS // 256, (c.IND_BITS % 256) // 8),
        "//",
        "// i.e. index PC %d, byte %d.%s"
        % (c.IND_BITS // 256, (c.IND_BITS % 256) // 8,
           ("  NOTE: PC %d holds NOTHING BUT these two bits --"
            "\n// the %d index bits fill PC0..%d exactly."
            % (c.IND_BITS // 256, c.IND_BITS, c.IND_BITS // 256 - 1))
           if c.IND_BITS % 256 == 0 else ""),
        "//",
        "// Get this wrong and the host reads a WRONG SPARSITY MODE off correct",
        "// hardware: the run completes, the numbers look plausible, and the reported",
        "// mode is a lie. Nothing errors.",
        "//",
        "// PATCH IT ONCE, DERIVED, AND NO CONFIG EVER NEEDS IT AGAIN. Three edits:",
        "//",
        "//   sparsity_at()  p[k * PC_BYTES + 16]  ->  p[k * PC_BYTES + SP_BYTE]",
        "//   both callers   sparsity_at(i_img[2], ...) -> sparsity_at(i_img[SP_PC], ...)",
        "//",
        "// After that the file is fully parameterised and every remaining config is a",
        "// constants change only.",
        "",
        "// For the Python packer (hex_to_bin.py / gen_timing_stimulus.py):",
        "//   W_PCS=%d  IND_PCS=%d  A_PCS=%d  C_PCS=%d"
        % (c.W_PCS, c.IND_PCS, c.A_PCS, c.C_PCS),
        "//   CORES=%d  BLOCKS=%d  LANES=%d  SPARSITY_BIT=%d  (bit of the joined word)"
        % (c.C, c.B, c.T, c.IND_BITS),
        ""])


# ===========================================================================
# BUILD.md
# ===========================================================================
def gen_build_md(c):
    ratio = c.target / c.ooc
    return """# Build: {tag}  ({C} cores x {B} blocks = {T} C Blocks)

Generated by `make_family_build.py`. Everything below is specific to this one
configuration; the engine VHDL, the two movers and all 7 IP cores are shared and
unchanged.

| | |
|---|---|
| C Blocks (T) | **{T}** |
| DSPs | **{dsps}** |
| MACs / cycle | {macs} |
| HBM pseudo-channels | **{total}** of 32 ({spare} spare) |
| -- weights | {W_PCS} ({wbits} bits) |
| -- indices | {IND_PCS} ({ibits} bits, {IND_BITS} used) |
| -- activations | {A_PCS} (512 bits -- always, the window is 32 elements) |
| -- output | {C_PCS} ({cbits} bits) |
| W_IDX | {W_IDX} (= 2 x BLOCKS_NUM) |
| OOC Fmax (bare engine, 1.5 ns) | {ooc:.1f} MHz |
| **in-system target** | **{target} MHz** ({ratio:.2f} x OOC) |

## Why {target} MHz

Only one in-system/OOC ratio has ever been measured on this design: 8x8 closed
**325 MHz in-system** against **448.6 MHz OOC**, a ratio of **0.724**. The gap is
the movers, the platform and the SLR crossings, none of which exist in an OOC
run of the bare engine.

{target} MHz is {ratio:.3f} x this config's OOC number, i.e. at or below that
measured ratio, then rounded down to a round value. Deliberately conservative:
a constraint that CLOSES establishes Fmax, a constraint that misses only
estimates it, and each build is hours. If this closes with margin to spare, the
next rung up is worth a second build -- record both.

## Files in this directory

| file | goes where | what it is |
|---|---|---|
| `Design/two2N_axis.vhd` | stays here; `gen_xo` reads it from here | AXIS splitter, {total} interfaces, generics defaulted to this config |
| `Design/krnl_gemv_sparse.v` | stays here | RTL kernel top, {total} stream ports |
| `gen_xo_{tag}.tcl` | `~/` on the server | packages `krnl_gemv_sparse_{tag}.xo` |
| `sparse_hbm_{tag}.cfg` | `~/Vitis/` | nk / sp= / stream_connect |
| `slr_floorplan_{tag}.cfg` | `~/Vitis/` | gemv->SLR1, movers->SLR0 |
| `host_defs_{tag}.h` | reference | the 6 constants for `host_sparse.cpp` |

## Steps

**1. Copy up.** SERVER LAYOUT, confirmed 2026-09-10 -- one directory per build:

    ~/GEMV_Sparse/              <- $repo; gen_xo writes the .xo HERE
    ~/GEMV_Sparse/Vitis/        <- the 300 MHz build (also Vitis_325/_350/_fix/_slr)
    ~/GEMV_Sparse/Vitis_{tag}/    <- NEW, this build

Each build directory carries its OWN copy of the mover .xo files, because v++
writes an `_x/` temp tree and two concurrent links in one directory corrupt each
other silently.

**THE MOVERS ALREADY EXIST -- DO NOT REBUILD THEM.** `krnl_mm2s.cpp` lives only
under `GEMV_Dense/Vitis/`; the sparse side has always copied the built `.xo` in,
because the movers are identical between the two designs and across every
configuration. Copy, never compile:

```bash
# on the server
cd ~/GEMV_Sparse && mkdir -p Vitis_{tag}
cp Vitis/krnl_mm2s.hw.xo Vitis/krnl_s2mm.hw.xo \\
   Vitis/krnl_mm2s.hw_emu.xo Vitis/krnl_s2mm.hw_emu.xo Vitis_{tag}/
df -h /home
```

```bash
# from the local repo root
scp -r family_builds/{tag} skoulas@coroni:/home/skoulas/GEMV_Sparse/family_builds/
scp family_builds/{tag}/gen_xo_{tag}.tcl skoulas@coroni:/home/skoulas/
scp family_builds/{tag}/sparse_hbm_{tag}.cfg \\
    family_builds/{tag}/slr_floorplan_{tag}.cfg \\
    Vitis/impl_family.cfg skoulas@coroni:/home/skoulas/GEMV_Sparse/Vitis_{tag}/
```

Environment, needed in EVERY fresh shell -- nothing below works without it:

```bash
source /opt/Xilinx/Vitis/2021.1/settings64.sh
source /opt/xilinx/xrt/setup.sh
export PLATFORM=/opt/xilinx/platforms/xilinx_u280_xdma_201920_3/xilinx_u280_xdma_201920_3.xpfm
export BDF=0000:af:00.1
```

**2. Package the .xo.** Vivado GUI -> Tcl Console, **no project open**:

```tcl
source /home/skoulas/gen_xo_{tag}.tcl
```

Produces `/home/skoulas/GEMV_Sparse/krnl_gemv_sparse_{tag}.xo`. **Verify by
listing the archive, never by size** -- a shell-only .xo links happily and
computes nothing:

```bash
unzip -l ~/GEMV_Sparse/krnl_gemv_sparse_{tag}.xo | grep -cE "\\.vhd|\\.xci"
```

Want **>= 18** (12 VHDL + 7 .xci, minus whatever the packager renames).

**3. hw_emu first.** From `~/GEMV_Sparse/Vitis_{tag}`. This config's channel topology ({total} streams) has never been
linked before. hw_emu catches a dangling `stream_connect` in under an hour;
the same mistake costs a whole `-t hw` build otherwise, and its only symptom
is a hang -- the barrier join waits forever on a channel that never arrives.

```bash
emconfigutil --platform $PLATFORM --nd 1     # writes emconfig.json -- do not skip
export XCL_EMULATION_MODE=hw_emu

v++ -t hw_emu --platform $PLATFORM --config sparse_hbm_{tag}.cfg \\
    --kernel_frequency {target} -l -o sparse_{tag}.hw_emu.xclbin \\
    ../krnl_gemv_sparse_{tag}.xo krnl_mm2s.hw_emu.xo krnl_s2mm.hw_emu.xo
```

No `impl_family.cfg` and no floorplan here -- this is simulation, placement and
strategy are irrelevant and only cost time. Run the host against it; **bit-exact
against golden.txt is the gate**. Then `unset XCL_EMULATION_MODE`.

**4. Link for hardware.** In `tmux`, from `~/GEMV_Sparse/Vitis_{tag}`:

```bash
v++ -t hw --platform $PLATFORM \\
    --config sparse_hbm_{tag}.cfg \\
    --config impl_family.cfg \\
    --config slr_floorplan_{tag}.cfg \\
    --kernel_frequency {target} \\
    -l -o sparse_{tag}_{target}.xclbin \\
    ../krnl_gemv_sparse_{tag}.xo krnl_mm2s.hw.xo krnl_s2mm.hw.xo
```

The mover `.xo` names carry the target: `krnl_mm2s.hw.xo` for `-t hw`,
`krnl_mm2s.hw_emu.xo` for `-t hw_emu`. They are built once and reused by every
config -- **do not rebuild them per configuration.**

**Check ~20 minutes in that the synthesis strategy landed** -- it was silently
ignored in every build before the 350 MHz one:

```bash
grep -oE "synth_design [^\\"]*" _x/logs/link/vivado.log | head -2
```

Want `-directive AlternateRoutability -no_lc -shreg_min_size 10` on that line.
A bare `synth_design -top pfm_dynamic -part ...` means the run name did not
match and only the implementation strategy applied -- the build is still valid,
it just is not testing what it claims to.

**5. Host.** Set the six constants from `host_defs_{tag}.h` at the top of
`host_sparse.cpp` and rebuild. Nothing else in that file changes -- it was
written against these constants throughout.

**6. Stimulus.** Set `W_PCS={W_PCS} IND_PCS={IND_PCS} A_PCS={A_PCS} CORES={C}
BLOCKS={B} SPARSITY_BIT={IND_BITS}` in `gen_timing_stimulus.py` and
`hex_to_bin.py`, then regenerate **before every measurement**. Stimulus is
shared mutable state on that server and a stale set measures the wrong matrix
at plausible-looking numbers.

## Pass criteria

- `unzip -l` shows the VHDL and the .xci, not just the wizard shell
- the link reports **WNS >= 0** at {target} MHz
- `xclbinutil --info` lists **{total}** HBM channels bound
- the host's first run is **bit-exact against golden.txt**
{extra}
""".format(tag=c.tag, C=c.C, B=c.B, T=c.T, dsps=c.dsps, macs=2 * c.T,
           total=c.total, spare=32 - c.total, W_PCS=c.W_PCS, IND_PCS=c.IND_PCS,
           A_PCS=c.A_PCS, C_PCS=c.C_PCS, W_IDX=c.W_IDX, IND_BITS=c.IND_BITS,
           wbits=c.W_PCS * 256, ibits=c.IND_PCS * 256, cbits=c.C_PCS * 256,
           ooc=c.ooc, target=c.target, ratio=ratio,
           extra=("\n## Note for this config\n\n"
                  "This is the 8x8 build that ALREADY EXISTS and already ran bit-exact\n"
                  "at 300 and 325 MHz. It is generated only as a regression check on the\n"
                  "generator. Diff the generated wrapper against the hand-written one:\n\n"
                  "```bash\n"
                  "diff <(grep -vE '^\\s*--|^\\s*$' family_builds/8x8/Design/two2N_axis.vhd) \\\\\n"
                  "     <(grep -vE '^\\s*--|^\\s*$' GEMV_4.0_Source/Design/two2N_axis.vhd)\n"
                  "```\n\n"
                  "Expect differences in ASSIGNMENT STYLE only -- element-wise slices here\n"
                  "against descending concatenation there. Same ports, same generics, same\n"
                  "instance. If a PORT or a GENERIC differs, the generator is wrong and\n"
                  "none of the other five should be built until it is fixed.\n"
                  if c.tag == "8x8" else ""))


# ===========================================================================
IMPL_FAMILY = '''# ----------------------------------------------------------------------------
# impl_family.cfg -- THE ONE strategy, used unchanged for all six family builds.
#
# GENERATED by make_family_build.py, and byte-identical in its [vivado] section
# to impl_opt_perf350.cfg -- the best-performing strategy this project has
# measured. Copied to a neutral name because "perf350" names an experiment, and
# this is now the frozen baseline for a six-point comparison.
#
# ⚠️ USE THIS FILE AND ONLY THIS FILE FOR ALL SIX. The whole point of the family
# study is that the six configurations differ in ONE thing -- their shape. If
# one of them gets a different strategy, its Fmax and its area are no longer
# comparable with the other five, and the DSP-vs-HBM-channel graph becomes a
# graph of two variables. Do not "just try Retiming" on the one that missed.
#
# ---- WHY THIS STRATEGY -----------------------------------------------------
# Measured, on this design, 2026-09-02:
#
#     target   dense    sparse
#     300      +0.022   0.000
#     325      +0.013   +0.001
#     350      +0.025   -0.204
#
# Dense went from +0.013 at 325 to +0.025 at 350 -- MORE margin at a 25 MHz
# tighter constraint -- and the synthesis strategy was the only change between
# those two builds. That is a clean attribution: Flow_AlternateRoutability
# works on this design.
#
# It was chosen on evidence rather than reputation. The worst path was ONE LUT6
# with fanout 7 spending 97% of its 3.955 ns on wire. One logic level spending
# 97% of its time on route is a ROUTABILITY problem, and
# Flow_AlternateRoutability targets exactly that (it expands to
# `-directive AlternateRoutability -no_lc -shreg_min_size 10`).
# Flow_PerfOptimized_high attacks LOGIC DEPTH, which was not the bottleneck --
# there was only one level of it.
#
# ---- THE RUN NAME IS THE TRAP ----------------------------------------------
# `v++ -l` builds the dynamic region as a DFX reconfigurable module, so the
# Vivado runs are `my_rm_synth_1` and `impl_1` -- NOT `synth_1`. A property
# addressed to a run that does not exist matches nothing and produces NO
# WARNING. Every build before 350 MHz set `run.synth_1.strategy` and it was
# silently ignored; those builds reached their frequency on the implementation
# strategy and the floorplan alone.
#
# Both spellings are given. An unmatched property is harmless, so covering both
# costs nothing.
#
# VERIFY IT LANDED, DO NOT ASSUME. Absence of an error is not evidence. ~20
# minutes into each build:
#
#   grep -oE "synth_design [^\\"]*" _x/logs/link/vivado.log | head -2
#
# Want `-directive AlternateRoutability -no_lc -shreg_min_size 10`. If the line
# is bare, the run name is wrong again and the build must be written up as
# implementation-strategy-only.
#
# ---- AREA CAVEAT -----------------------------------------------------------
# `-no_lc` (no LUT combining) and `-shreg_min_size 10` shift LUT and FF counts.
# That is FINE here because all six get it -- the comparison is internally
# consistent. It does mean the six area numbers are NOT directly comparable
# with the OOC sweep numbers in GEMV_Family_AllPoints.csv, which had no
# synthesis strategy at all. Report them as two separate columns, never as one
# series.
#
# ---- BUILD TIME ------------------------------------------------------------
# 300 MHz with phys-opt alone took 3h26m. Expect 6-12 h per config with
# aggressive place and route on top. tmux, and check `df -h /home` first.
# ----------------------------------------------------------------------------

[vivado]

# ---- synthesis: both run spellings, only one will match --------------------
prop=run.my_rm_synth_1.strategy=Flow_AlternateRoutability
prop=run.synth_1.strategy=Flow_AlternateRoutability

# ---- implementation --------------------------------------------------------
prop=run.impl_1.strategy=Performance_ExplorePostRoutePhysOpt

# one targeted override: routing is the measured bottleneck, so spend there
prop=run.impl_1.STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE=AggressiveExplore
'''


def gen_readme(cfgs):
    rows = []
    for c in cfgs:
        rows.append("| {t} | {C}x{B} | {T} | {d} | **{n}** | {s} | {o:.1f} | **{g}** | {p:.2f} |"
                    .format(t=c.tag, C=c.C, B=c.B, T=c.T, d=c.dsps, n=c.total,
                            s=32 - c.total, o=c.ooc, g=c.target,
                            p=2 * c.T * c.target / 1000.0))
    return """# Family bitstream campaign -- six configurations, one strategy

Generated by `make_family_build.py`. One directory per configuration, each with
the seven files that differ between builds. **Everything else is shared and
unchanged**: the engine VHDL, `krnl_mm2s.cpp`, `krnl_s2mm.cpp`, and all 7 IP
cores.

| tag | CxB | T | DSP | HBM PC | spare | OOC MHz | target | GMAC/s @ target |
|---|---|---|---|---|---|---|---|---|
{rows}

## The one strategy

All six use `impl_family.cfg` **unchanged** -- `Flow_AlternateRoutability`
synthesis on `my_rm_synth_1`, `Performance_ExplorePostRoutePhysOpt`
implementation, `AggressiveExplore` routing -- plus each config's own
`slr_floorplan_<tag>.cfg` (gemv to SLR1, movers to SLR0).

That is the best strategy this project has measured, and the attribution is
clean: dense went from +0.013 ns at 325 MHz to **+0.025 ns at 350 MHz** -- more
margin at a tighter constraint -- with the synthesis strategy as the only
change between those two builds.

**Do not vary it per config.** Six points that differ in one variable make a
graph; six points that differ in two make an anecdote. If one config misses its
target, the answer is a lower target for that config, not a different strategy.

## Build order

Build **4x32 (T=128) first**, not last. It is the one most likely to fail, for
two reasons worth knowing before you spend six hours:

1. **It uses all 32 HBM channels**, one of which carries two bits of sparsity
   code in an otherwise-empty pseudo-channel. There is no spare channel to
   trade if the link needs one.
2. **32 movers land in SLR0.** At roughly 13.5 BRAM each that is ~432 BRAM on
   top of the platform's 204, against SLR0's 720 -- about 88%. If
   `place_design` fails with an over-utilisation error naming SLR0, delete the
   `slr=mm2s*` / `slr=s2mm*` lines from `slr_floorplan_4x32.cfg` and keep only
   `slr=gemv:SLR1`. The movers follow their HBM ports to SLR0 anyway; they just
   will not be hard-pinned there.

Then **8x8**, which needs no build at all -- it already exists and already ran
bit-exact at 300 and 325 MHz. Use it to check the generator (see
`8x8/BUILD.md`) and reuse its measured numbers.

Then the remaining four in any order. Four builds at 6-12 h each.

## Targets, and why they are conservative

Only one in-system/OOC ratio has ever been measured here: 8x8 closed **325 MHz
in-system** against **448.6 MHz OOC** = **0.724**. The gap is the movers, the
platform and the SLR crossings -- none of which exist in an OOC run of the bare
engine.

Every target above is at or below 0.724 x that config's OOC number, rounded
down. That is deliberate: **a constraint that closes establishes Fmax; a
constraint that misses only estimates it**, and this project already has a
measured counterexample where a 300 MHz target achieved a BETTER period than a
250 MHz one. Six clean closes are worth more than six aggressive attempts.

If a config closes with real margin, one rung up is a cheap second build --
record both numbers.

## The mover ceiling -- watch for this

`run_hls_movers.tcl` builds the movers at **3.333 ns (300 MHz)**. The critical
path in earlier builds has already been an HLS mover's FSM register
(`mm2s_w2/ap_CS_fsm_reg[71]`). The two fastest configs here target 400 and 375
MHz, well past what the movers were synthesised for.

If 4x4 or 8x4 misses timing and the failing path is inside a mover rather than
inside `gemv`, that is the cause -- rebuild the movers at a tighter period
(`set PERIOD 2.5` in `run_hls_movers.tcl`) rather than lowering the target.
Check where the failure is before deciding.

## After each build

Record in `results/`: achieved WNS, the utilisation report, and the measured
throughput and power. The six-point series is the deliverable -- DSPs and HBM
channels against achieved clock and GMAC/s per channel.

⚠️ Area from these builds is **not** comparable with the OOC numbers in
`GEMV_Family_AllPoints.csv`: `-no_lc` and `-shreg_min_size 10` apply here and
did not there. Two columns, never one series.
""".format(rows="\n".join(rows))


def emit(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return len(text.split("\n"))


def main():
    want = sys.argv[1:]
    cfgs = [Cfg(*x) for x in CONFIGS]
    if want:
        cfgs = [c for c in cfgs if c.tag in want]
        if not cfgs:
            print("no such config; known:", ", ".join(t for t, _, _, _, _ in CONFIGS))
            return

    emit(os.path.join(ROOT, "Vitis", "impl_family.cfg"), IMPL_FAMILY)
    print("wrote Vitis/impl_family.cfg   (shared by all six -- one strategy, one variable)\n")

    for c in cfgs:
        d = os.path.join(OUTROOT, c.tag)
        n = 0
        n += emit(os.path.join(d, "Design", "two2N_axis.vhd"), gen_vhd(c))
        n += emit(os.path.join(d, "Design", "krnl_gemv_sparse.v"), gen_verilog(c))
        n += emit(os.path.join(d, "gen_xo_%s.tcl" % c.tag), gen_xo_tcl(c))
        n += emit(os.path.join(d, "sparse_hbm_%s.cfg" % c.tag), gen_link_cfg(c))
        n += emit(os.path.join(d, "slr_floorplan_%s.cfg" % c.tag),
                  gen_slr_cfg_slr0(c))
        n += emit(os.path.join(d, "slr_floorplan_%s_split.cfg" % c.tag),
                  gen_slr_cfg(c))
        n += emit(os.path.join(d, "host_defs_%s.h" % c.tag), gen_host_defs(c))
        n += emit(os.path.join(d, "BUILD.md"), gen_build_md(c))
        print("  %-6s %2dc x %2db = %3d blocks | %4d DSP | %2d PC "
              "(%2dw %2di %da %2dc) | target %3d MHz | %d lines"
              % (c.tag, c.C, c.B, c.T, c.dsps, c.total, c.W_PCS, c.IND_PCS,
                 c.A_PCS, c.C_PCS, c.target, n))

    if not want:
        emit(os.path.join(OUTROOT, "README.md"), gen_readme(cfgs))
        print("\nwrote family_builds/README.md  (campaign plan + build order)")
    print("\n  family_builds/<tag>/ -- 7 files each. Movers, IP and engine VHDL unchanged.")


if __name__ == "__main__":
    main()
