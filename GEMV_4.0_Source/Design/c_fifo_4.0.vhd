library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
--
-- Description:
-- Output buffer for the GEMV 4.0 datapath -- a FORK built from AXI4-Stream Data
-- FIFOs. The C cores produce one co-valid result beat (all lanes valid on
-- Cvalid); it is split into OUT_PCS per-PC AXIS Data FIFOs, each draining
-- independently to its own HBM write pseudo-channel over an AXIS master.
--
-- Using axis_data_fifo here means:
--   * the m_axis side IS the output PC's AXIS master -- passes straight through,
--     no manual tvalid/rd_en/empty logic;
--   * TLAST (Ctlast) is a native sideband, not a packed bit;
--   * TREADY backpressure on the read side is handled by the IP.
--
-- Backpressure contract (unchanged): the C cores cannot be backpressured, so the
-- write-side s_axis_tready is IGNORED (left open) -- a co-valid Cvalid writes
-- every PC FIFO unconditionally. The module instead exposes prog_full (OR of the
-- per-PC flags); the TOP-LEVEL global gate must stop feeding inputs on prog_full
-- so no PC FIFO ever actually fills (a dropped slice would desync the PCs).
--
-- IP configuration (AXI4-Stream Data FIFO, one per PC):
--   * TDATA width = PC_WIDTH (256 b = 32 bytes), TLAST enabled, no TKEEP/TUSER.
--   * Common clock (synchronous), active-low aresetn, NON-packet mode.
--   * Depth ~256 (Memory type Auto -> BRAM). Sized to ride one HBM WRITE refresh,
--     not "once per sweep": at 2:32 each block is independent, so a full result
--     beat streams EVERY cycle (256 b/cycle per PC). Output is bandwidth-matched
--     1:1 to the write PC, so the FIFO smooths a refresh burst (~110-160 cyc)
--     but cannot out-run it. Denser modes produce less often and under-fill the
--     same depth, so size for the 2:32 worst case.
--   * Programmable Full enabled, threshold ~depth-16 (~240): let it fill during a
--     write refresh and throttle the engine (via the gate) only near the top.
-- ----------------------------------------------------------------------------

entity c_fifo is
    generic(
        PC_WIDTH : integer := 256;      -- HBM pseudo-channel data width
        OUT_PCS  : integer := 4         -- output PCs (4*256 = 1024 bits = 2:32 max)
    );
    port(
        clk     : in std_logic;
        resetn  : in std_logic;         -- active-low (tied to AXIS aresetn)

        -- ---- write side: from the C cores (one co-valid result beat) ----
        Cout      : in  std_logic_vector(OUT_PCS*PC_WIDTH-1 downto 0);
        Cvalid    : in  std_logic;
        Ctlast    : in  std_logic;
        prog_full : out std_logic;      -- to the global gate (any PC near full)

        -- ---- read side: one AXI-Stream master per output PC ----
        m_axis_c_tdata  : out std_logic_vector(OUT_PCS*PC_WIDTH-1 downto 0);
        m_axis_c_tvalid : out std_logic_vector(OUT_PCS-1 downto 0);
        m_axis_c_tlast  : out std_logic_vector(OUT_PCS-1 downto 0);
        m_axis_c_tready : in  std_logic_vector(OUT_PCS-1 downto 0)
    );
end entity c_fifo;

architecture c_fifo_arch of c_fifo is

    -- AXI4-Stream Data FIFO: PC_WIDTH-bit TDATA + TLAST, common clock,
    -- active-low aresetn, prog_full enabled. (Generate in the IP catalog.)
    component axis_data_fifo_c is
        port(
            s_axis_aclk    : in  std_logic;
            s_axis_aresetn : in  std_logic;
            s_axis_tvalid  : in  std_logic;
            s_axis_tready  : out std_logic;
            s_axis_tdata   : in  std_logic_vector(PC_WIDTH-1 downto 0);
            s_axis_tlast   : in  std_logic;
            m_axis_tvalid  : out std_logic;
            m_axis_tready  : in  std_logic;
            m_axis_tdata   : out std_logic_vector(PC_WIDTH-1 downto 0);
            m_axis_tlast   : out std_logic;
            prog_full      : out std_logic
        );
    end component axis_data_fifo_c;

    signal c_pfull : std_logic_vector(OUT_PCS-1 downto 0);

begin

    -- Stop the engine (via the top-level gate) when ANY PC FIFO is near full,
    -- so the all-or-nothing co-valid write never overflows one of them.
    prog_full <= '1' when (unsigned(c_pfull) /= 0) else '0';

    C_FIFOS : for i in 0 to OUT_PCS-1 generate
        C_FIFO_INST : axis_data_fifo_c
        port map(
            s_axis_aclk    => clk,
            s_axis_aresetn => resetn,                 -- active-low, no inversion

            -- write side: co-valid beat from the cores; tready ignored (cores
            -- cannot be backpressured -> rely on prog_full + the global gate)
            s_axis_tvalid  => Cvalid,
            s_axis_tready  => open,
            s_axis_tdata   => Cout((i+1)*PC_WIDTH-1 downto i*PC_WIDTH),
            s_axis_tlast   => Ctlast,

            -- read side: straight through to this PC's output AXIS master
            m_axis_tvalid  => m_axis_c_tvalid(i),
            m_axis_tready  => m_axis_c_tready(i),
            m_axis_tdata   => m_axis_c_tdata((i+1)*PC_WIDTH-1 downto i*PC_WIDTH),
            m_axis_tlast   => m_axis_c_tlast(i),

            prog_full      => c_pfull(i)
        );
    end generate C_FIFOS;

end architecture c_fifo_arch;
