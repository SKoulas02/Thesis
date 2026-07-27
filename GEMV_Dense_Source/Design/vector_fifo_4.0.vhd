library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
--
-- Description:
-- HBM ingress + alignment for the activation stream, built from AXI4-Stream Data
-- FIFOs. One per HBM pseudo-channel (PC): the read engine's AXIS master drives
-- each FIFO's s_axis (native TREADY backpressure -- no manual wr_en/full glue).
-- A barrier JOIN on the m_axis side presents one aligned 512-bit word only when
-- every PC FIFO has a beat: all PCs are popped together (shared tready) so there
-- is no reordering, and TLAST (end-of-vector) is a native AXIS sideband.
--
-- Read side keeps the join handshake (ready / rd_en) the gate + replay buffer
-- expect: ready = all PCs valid; the consumer pops with rd_en.
--
-- This is the ingress stage only; A_vector_out feeds both compute and the
-- downstream replay/recirculation buffer (vector_cycle_512).
--
-- IP configuration (AXI4-Stream Data FIFO, one per PC -- "axis_data_fifo_v"):
--   * TDATA width = PC_WIDTH (256 b = 32 bytes), TLAST enabled, no TKEEP/TUSER.
--   * Common clock (synchronous), active-low aresetn, NON-packet mode.
--   * Depth 512 (Memory type Auto -> BRAM): an 8K-element vector is 256 beats
--     per PC (8192 * 16 bit / 512-bit beat); 512 holds the whole vector with
--     margin (full residency even with no simultaneous drain). Sized to the
--     vector, not to refresh -- it loads once then the channel stops.
--   * No prog_full needed: s_axis_tready backpressures the read engine natively.
--
-- PC mapping (host packs HBM this way):
--   activations : PC k -> A_vector_out[(k+1)*256-1 : k*256]
-- ----------------------------------------------------------------------------

entity vector_fifo_512 is
    generic(
        PC_WIDTH : integer := 256;      -- HBM pseudo-channel data width
        A_PCS    : integer := 2         -- activation PCs (2*256 = 512 bits)
    );
    port(
        clk     : in std_logic;
        resetn  : in std_logic;         -- active-low (tied to AXIS aresetn)

        -- ---- write side: AXI-Stream slave, one stream per PC (from read engine) ----
        s_axis_a_tdata  : in  std_logic_vector(A_PCS*PC_WIDTH-1 downto 0);
        s_axis_a_tvalid : in  std_logic_vector(A_PCS-1 downto 0);
        s_axis_a_tready : out std_logic_vector(A_PCS-1 downto 0);
        s_axis_a_tlast  : in  std_logic_vector(A_PCS-1 downto 0);   -- per-PC end-of-vector

        -- ---- read side: one aligned word (join handshake) ----
        rd_en        : in  std_logic;                                    -- consumer pop
        ready        : out std_logic;                                    -- all PCs have a beat
        A_vector_out : out std_logic_vector(A_PCS*PC_WIDTH-1 downto 0);   -- 512
        tlast_out    : out std_logic
    );
end entity vector_fifo_512;

architecture vector_fifo_arch of vector_fifo_512 is

    -- AXI4-Stream Data FIFO: PC_WIDTH-bit TDATA + TLAST, common clock,
    -- active-low aresetn. (Generate in the IP catalog.)
    component axis_data_fifo_v is
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
            m_axis_tlast   : out std_logic
        );
    end component axis_data_fifo_v;

    signal m_tvalid    : std_logic_vector(A_PCS-1 downto 0);
    signal tlast_bits  : std_logic_vector(A_PCS-1 downto 0);
    signal all_valid   : std_logic;
    signal pop         : std_logic;

begin

    -- Barrier: an aligned beat exists only when every PC FIFO has data.
    all_valid <= '1' when (m_tvalid = (m_tvalid'range => '1')) else '0';

    ready <= all_valid;
    pop   <= rd_en and all_valid;   -- pop every PC together, never partially

    -- tlast of the joined beat: last only when every head's tlast is set.
    tlast_out <= '1' when (all_valid = '1' and tlast_bits = (tlast_bits'range => '1'))
                 else '0';

    A_FIFOS : for i in 0 to A_PCS-1 generate
        A_FIFO_INST : axis_data_fifo_v
        port map(
            s_axis_aclk    => clk,
            s_axis_aresetn => resetn,

            -- write side: this PC's ingress stream (native backpressure)
            s_axis_tvalid  => s_axis_a_tvalid(i),
            s_axis_tready  => s_axis_a_tready(i),
            s_axis_tdata   => s_axis_a_tdata((i+1)*PC_WIDTH-1 downto i*PC_WIDTH),
            s_axis_tlast   => s_axis_a_tlast(i),

            -- read side: feeds the join; popped together via 'pop'
            m_axis_tvalid  => m_tvalid(i),
            m_axis_tready  => pop,
            m_axis_tdata   => A_vector_out((i+1)*PC_WIDTH-1 downto i*PC_WIDTH),
            m_axis_tlast   => tlast_bits(i)
        );
    end generate A_FIFOS;

end architecture vector_fifo_arch;
