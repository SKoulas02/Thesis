library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
--
-- dense_gemv_axis -- AXI4-Stream splitter wrapper around dense_gemv.
--
-- WHY THIS EXISTS (INTEGRATION_PLAN.md 0.2b):
-- dense_gemv declares its streams BUNDLED -- s_axis_w_tdata is one 2048-bit port
-- with an 8-bit tvalid/tready/tlast vector, i.e. 8 logical AXI-Stream channels in
-- a single declaration, and m_axis_c_tdata is one 1024-bit port with 4-bit
-- vectors. The Vitis RTL Kernel Wizard needs 14 SEPARATE, properly named AXIS
-- interfaces so the packager can infer them.
--
-- THIS FILE CONTAINS NO LOGIC. It is pure re-wiring:
--   1. slice tdata little-endian            -- PC k = tdata(256k+255 downto 256k)
--   2. slice the tvalid/tready/tlast vectors to scalars -- bit k = PC k
--   3. drive every master tkeep to all-1s; ignore every slave tkeep
--      (UG1393: TKEEP must be all 1s when TLAST is 0 and may never be all zeros;
--       this engine has no ragged tails -- every beat is a full 256-bit PC word)
--   4. supply nothing else -- no registers, no gating, no state
-- The verified dense_gemv netlist below is untouched, so S4 must report
-- utilisation and WNS IDENTICAL to the pre-wrapper numbers. A wrapper that costs
-- a LUT is a wrapper with logic in it.
--
-- PORT NAMING follows the RTL Kernel Wizard: ap_clk / ap_rst_n, and per stream
-- <name>_tdata/_tvalid/_tready/_tkeep/_tlast, with <name> exactly as configured
-- in the wizard (S8a) -- so this entity drops into krnl_gemv_dense_example's
-- place with no adapter and the stream_connect strings in the link config match
-- these port names one-for-one.
-- ----------------------------------------------------------------------------

entity dense_gemv_axis is
    generic(
        EL_SIZE     : integer := 16;    -- Bit size of each element
        W_IDX       : integer := 16;    -- Number of matrix elements per core
        A_IDX       : integer := 32;    -- Number of vector elements (the window)
        BLOCKS_NUM  : integer := 8;     -- Number of c blocks (2 elements each)
        CORES_NUM   : integer := 8;     -- Number of C Cores

        PC_WIDTH    : integer := 256;   -- HBM pseudo-channel data width
        W_PCS       : integer := 8;     -- weight PCs
        A_PCS       : integer := 2;     -- activation PCs
        C_PCS       : integer := 4      -- output PCs
    );
    port(
        ap_clk   : in std_logic;
        ap_rst_n : in std_logic;

        -- ---- Weights : 8 slave AXIS channels ----
        s_axis_w0_tdata  : in  std_logic_vector(PC_WIDTH-1 downto 0);
        s_axis_w0_tkeep  : in  std_logic_vector(PC_WIDTH/8-1 downto 0);
        s_axis_w0_tvalid : in  std_logic;
        s_axis_w0_tready : out std_logic;
        s_axis_w0_tlast  : in  std_logic;

        s_axis_w1_tdata  : in  std_logic_vector(PC_WIDTH-1 downto 0);
        s_axis_w1_tkeep  : in  std_logic_vector(PC_WIDTH/8-1 downto 0);
        s_axis_w1_tvalid : in  std_logic;
        s_axis_w1_tready : out std_logic;
        s_axis_w1_tlast  : in  std_logic;

        s_axis_w2_tdata  : in  std_logic_vector(PC_WIDTH-1 downto 0);
        s_axis_w2_tkeep  : in  std_logic_vector(PC_WIDTH/8-1 downto 0);
        s_axis_w2_tvalid : in  std_logic;
        s_axis_w2_tready : out std_logic;
        s_axis_w2_tlast  : in  std_logic;

        s_axis_w3_tdata  : in  std_logic_vector(PC_WIDTH-1 downto 0);
        s_axis_w3_tkeep  : in  std_logic_vector(PC_WIDTH/8-1 downto 0);
        s_axis_w3_tvalid : in  std_logic;
        s_axis_w3_tready : out std_logic;
        s_axis_w3_tlast  : in  std_logic;

        s_axis_w4_tdata  : in  std_logic_vector(PC_WIDTH-1 downto 0);
        s_axis_w4_tkeep  : in  std_logic_vector(PC_WIDTH/8-1 downto 0);
        s_axis_w4_tvalid : in  std_logic;
        s_axis_w4_tready : out std_logic;
        s_axis_w4_tlast  : in  std_logic;

        s_axis_w5_tdata  : in  std_logic_vector(PC_WIDTH-1 downto 0);
        s_axis_w5_tkeep  : in  std_logic_vector(PC_WIDTH/8-1 downto 0);
        s_axis_w5_tvalid : in  std_logic;
        s_axis_w5_tready : out std_logic;
        s_axis_w5_tlast  : in  std_logic;

        s_axis_w6_tdata  : in  std_logic_vector(PC_WIDTH-1 downto 0);
        s_axis_w6_tkeep  : in  std_logic_vector(PC_WIDTH/8-1 downto 0);
        s_axis_w6_tvalid : in  std_logic;
        s_axis_w6_tready : out std_logic;
        s_axis_w6_tlast  : in  std_logic;

        s_axis_w7_tdata  : in  std_logic_vector(PC_WIDTH-1 downto 0);
        s_axis_w7_tkeep  : in  std_logic_vector(PC_WIDTH/8-1 downto 0);
        s_axis_w7_tvalid : in  std_logic;
        s_axis_w7_tready : out std_logic;
        s_axis_w7_tlast  : in  std_logic;

        -- ---- Activations : 2 slave AXIS channels ----
        s_axis_a0_tdata  : in  std_logic_vector(PC_WIDTH-1 downto 0);
        s_axis_a0_tkeep  : in  std_logic_vector(PC_WIDTH/8-1 downto 0);
        s_axis_a0_tvalid : in  std_logic;
        s_axis_a0_tready : out std_logic;
        s_axis_a0_tlast  : in  std_logic;

        s_axis_a1_tdata  : in  std_logic_vector(PC_WIDTH-1 downto 0);
        s_axis_a1_tkeep  : in  std_logic_vector(PC_WIDTH/8-1 downto 0);
        s_axis_a1_tvalid : in  std_logic;
        s_axis_a1_tready : out std_logic;
        s_axis_a1_tlast  : in  std_logic;

        -- ---- Output : 4 master AXIS channels ----
        m_axis_c0_tdata  : out std_logic_vector(PC_WIDTH-1 downto 0);
        m_axis_c0_tkeep  : out std_logic_vector(PC_WIDTH/8-1 downto 0);
        m_axis_c0_tvalid : out std_logic;
        m_axis_c0_tready : in  std_logic;
        m_axis_c0_tlast  : out std_logic;

        m_axis_c1_tdata  : out std_logic_vector(PC_WIDTH-1 downto 0);
        m_axis_c1_tkeep  : out std_logic_vector(PC_WIDTH/8-1 downto 0);
        m_axis_c1_tvalid : out std_logic;
        m_axis_c1_tready : in  std_logic;
        m_axis_c1_tlast  : out std_logic;

        m_axis_c2_tdata  : out std_logic_vector(PC_WIDTH-1 downto 0);
        m_axis_c2_tkeep  : out std_logic_vector(PC_WIDTH/8-1 downto 0);
        m_axis_c2_tvalid : out std_logic;
        m_axis_c2_tready : in  std_logic;
        m_axis_c2_tlast  : out std_logic;

        m_axis_c3_tdata  : out std_logic_vector(PC_WIDTH-1 downto 0);
        m_axis_c3_tkeep  : out std_logic_vector(PC_WIDTH/8-1 downto 0);
        m_axis_c3_tvalid : out std_logic;
        m_axis_c3_tready : in  std_logic;
        m_axis_c3_tlast  : out std_logic
    );
end entity dense_gemv_axis;


architecture rewire of dense_gemv_axis is

    -- bundled buses, exactly as dense_gemv declares them
    signal w_tdata  : std_logic_vector(W_PCS*PC_WIDTH-1 downto 0);
    signal w_tvalid : std_logic_vector(W_PCS-1 downto 0);
    signal w_tready : std_logic_vector(W_PCS-1 downto 0);
    signal w_tlast  : std_logic_vector(W_PCS-1 downto 0);

    signal a_tdata  : std_logic_vector(A_PCS*PC_WIDTH-1 downto 0);
    signal a_tvalid : std_logic_vector(A_PCS-1 downto 0);
    signal a_tready : std_logic_vector(A_PCS-1 downto 0);
    signal a_tlast  : std_logic_vector(A_PCS-1 downto 0);

    signal c_tdata  : std_logic_vector((BLOCKS_NUM*CORES_NUM*EL_SIZE)-1 downto 0);
    signal c_tvalid : std_logic_vector(C_PCS-1 downto 0);
    signal c_tready : std_logic_vector(C_PCS-1 downto 0);
    signal c_tlast  : std_logic_vector(C_PCS-1 downto 0);

    constant KEEP_ALL : std_logic_vector(PC_WIDTH/8-1 downto 0) := (others => '1');

begin

    -- =======================================================================
    -- 1. JOIN the per-channel slave ports into the bundled buses.
    --    Little-endian: PC 0 occupies the LSBs, matching HBM/AXI byte order
    --    and the convention every other module in this design follows.
    -- =======================================================================
    w_tdata  <= s_axis_w7_tdata  & s_axis_w6_tdata  & s_axis_w5_tdata  & s_axis_w4_tdata  &
                s_axis_w3_tdata  & s_axis_w2_tdata  & s_axis_w1_tdata  & s_axis_w0_tdata;

    w_tvalid <= s_axis_w7_tvalid & s_axis_w6_tvalid & s_axis_w5_tvalid & s_axis_w4_tvalid &
                s_axis_w3_tvalid & s_axis_w2_tvalid & s_axis_w1_tvalid & s_axis_w0_tvalid;

    w_tlast  <= s_axis_w7_tlast  & s_axis_w6_tlast  & s_axis_w5_tlast  & s_axis_w4_tlast  &
                s_axis_w3_tlast  & s_axis_w2_tlast  & s_axis_w1_tlast  & s_axis_w0_tlast;

    a_tdata  <= s_axis_a1_tdata  & s_axis_a0_tdata;
    a_tvalid <= s_axis_a1_tvalid & s_axis_a0_tvalid;
    a_tlast  <= s_axis_a1_tlast  & s_axis_a0_tlast;

    -- slave tkeep is deliberately unused: every beat is a full PC word, so the
    -- engine has no use for byte enables. Reading them would be the only way
    -- this wrapper could acquire logic.

    -- =======================================================================
    -- 2. FAN OUT the bundled tready back to the per-channel slave ports.
    -- =======================================================================
    s_axis_w0_tready <= w_tready(0);
    s_axis_w1_tready <= w_tready(1);
    s_axis_w2_tready <= w_tready(2);
    s_axis_w3_tready <= w_tready(3);
    s_axis_w4_tready <= w_tready(4);
    s_axis_w5_tready <= w_tready(5);
    s_axis_w6_tready <= w_tready(6);
    s_axis_w7_tready <= w_tready(7);

    s_axis_a0_tready <= a_tready(0);
    s_axis_a1_tready <= a_tready(1);

    -- =======================================================================
    -- 3. SPLIT the bundled output bus into the 4 master channels, and drive
    --    tkeep all-1s (UG1393 requires it; no ragged tails exist here).
    -- =======================================================================
    m_axis_c0_tdata  <= c_tdata(1*PC_WIDTH-1 downto 0*PC_WIDTH);
    m_axis_c1_tdata  <= c_tdata(2*PC_WIDTH-1 downto 1*PC_WIDTH);
    m_axis_c2_tdata  <= c_tdata(3*PC_WIDTH-1 downto 2*PC_WIDTH);
    m_axis_c3_tdata  <= c_tdata(4*PC_WIDTH-1 downto 3*PC_WIDTH);

    m_axis_c0_tvalid <= c_tvalid(0);
    m_axis_c1_tvalid <= c_tvalid(1);
    m_axis_c2_tvalid <= c_tvalid(2);
    m_axis_c3_tvalid <= c_tvalid(3);

    m_axis_c0_tlast  <= c_tlast(0);
    m_axis_c1_tlast  <= c_tlast(1);
    m_axis_c2_tlast  <= c_tlast(2);
    m_axis_c3_tlast  <= c_tlast(3);

    m_axis_c0_tkeep  <= KEEP_ALL;
    m_axis_c1_tkeep  <= KEEP_ALL;
    m_axis_c2_tkeep  <= KEEP_ALL;
    m_axis_c3_tkeep  <= KEEP_ALL;

    c_tready <= m_axis_c3_tready & m_axis_c2_tready & m_axis_c1_tready & m_axis_c0_tready;

    -- =======================================================================
    -- 4. The verified engine, untouched.
    -- =======================================================================
    DUT : entity work.dense_gemv
        generic map (
            EL_SIZE    => EL_SIZE,
            W_IDX      => W_IDX,
            A_IDX      => A_IDX,
            BLOCKS_NUM => BLOCKS_NUM,
            CORES_NUM  => CORES_NUM,
            PC_WIDTH   => PC_WIDTH,
            W_PCS      => W_PCS,
            A_PCS      => A_PCS,
            C_PCS      => C_PCS
        )
        port map (
            clk             => ap_clk,
            resetn          => ap_rst_n,

            s_axis_w_tdata  => w_tdata,
            s_axis_w_tvalid => w_tvalid,
            s_axis_w_tready => w_tready,
            s_axis_w_tlast  => w_tlast,

            s_axis_a_tdata  => a_tdata,
            s_axis_a_tvalid => a_tvalid,
            s_axis_a_tlast  => a_tlast,
            s_axis_a_tready => a_tready,

            m_axis_c_tdata  => c_tdata,
            m_axis_c_tvalid => c_tvalid,
            m_axis_c_tlast  => c_tlast,
            m_axis_c_tready => c_tready
        );

end architecture rewire;
