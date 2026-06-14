library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
--
-- Description:
-- HBM ingress + alignment for the weight / index / sparsity streams, built from
-- AXI4-Stream Data FIFOs. One per HBM pseudo-channel (PC): the read engine's
-- AXIS master drives each FIFO's s_axis (native TREADY backpressure -- no manual
-- wr_en/full glue). A barrier JOIN on the m_axis side presents one aligned word
-- only when every PC FIFO has a beat: all PCs popped together (shared tready) ->
-- no reordering.
--
-- Per aligned beat the join delivers 2048 weight bits + 640 index bits + 2
-- sparsity bits (the sparsity rides in the index PC2 padding, bits [641:640] of
-- the join -- no separate stream).
--
-- The weight stream also carries TLAST = end of the weight matrix. tlast_out
-- pulses on the beat the LAST weight beat is popped (= no more work). This is a
-- pre-pipeline marker: the TOP aligns it with the drained output (the activation
-- lap tlast / Ctlast path) to set the final output TLAST -- it must NOT be used
-- raw as "done", since the last result emerges pipeline-latency later.
--
-- Read side keeps the join handshake (ready / rd_en) the gate expects.
--
-- IP configuration (AXI4-Stream Data FIFO, one per PC -- "axis_data_fifo_pc",
-- used for BOTH weight and index PCs since both are 256-bit):
--   * TDATA width = PC_WIDTH (256 b = 32 bytes), TLAST enabled (carries the
--     end-of-calculation marker on the weight stream), no TKEEP/TUSER.
--     Index PCs share the IP; their s_axis_tlast is tied off (unused).
--   * Common clock (synchronous), active-low aresetn, NON-packet mode.
--   * Depth 512 (Memory type Auto -> BRAM): weights/indices are consumed EVERY
--     cycle, so size to ride one HBM read refresh (~110-160 cyc) + skew, like a
--     continuously-streamed buffer (NOT the one-shot activation sizing).
--   * No prog_full needed: s_axis_tready backpressures the read engine natively;
--     the join uses the empty side (m_axis_tvalid) via 'ready'.
--
-- PC mapping (host packs HBM this way):
--   weights : PC k -> W_out[(k+1)*256-1 : k*256]  (= core k's 16 weights)
--   indices : the 640 useful bits live in the LOW 640 of the 3*256 join.
--   sparsity: 2 bits in the index PC2 padding -> ind_concat[641:640].
-- ----------------------------------------------------------------------------

entity weights_fifo_2k is
    generic(
        PC_WIDTH   : integer := 256;    -- HBM pseudo-channel data width
        W_PCS      : integer := 8;      -- weight PCs (8*256 = 2048 bits)
        IND_PCS    : integer := 3;      -- index  PCs (3*256 =  768 bits, 640 used)
        IND_BITS   : integer := 640     -- useful index bits (remainder is padding)
    );
    port(
        clk     : in std_logic;
        resetn  : in std_logic;         -- active-low (tied to AXIS aresetn)

        -- ---- write side: AXI-Stream slave, one stream per PC (from read engine) ----
        s_axis_w_tdata    : in  std_logic_vector(W_PCS*PC_WIDTH-1 downto 0);
        s_axis_w_tvalid   : in  std_logic_vector(W_PCS-1 downto 0);
        s_axis_w_tready   : out std_logic_vector(W_PCS-1 downto 0);
        s_axis_w_tlast    : in  std_logic_vector(W_PCS-1 downto 0);   -- end of weight matrix

        s_axis_ind_tdata  : in  std_logic_vector(IND_PCS*PC_WIDTH-1 downto 0);
        s_axis_ind_tvalid : in  std_logic_vector(IND_PCS-1 downto 0);
        s_axis_ind_tready : out std_logic_vector(IND_PCS-1 downto 0);

        -- ---- read side: one aligned word (join handshake) ----
        rd_en        : in  std_logic;                                    -- consumer pop
        ready        : out std_logic;                                    -- all PCs have a beat
        tlast_out    : out std_logic;                                    -- last weight beat popped
        W_out        : out std_logic_vector(W_PCS*PC_WIDTH-1 downto 0);   -- 2048
        indices_out  : out std_logic_vector(IND_BITS-1 downto 0);        -- 640
        Sparsity_out : out std_logic_vector(1 downto 0)
    );
end entity weights_fifo_2k;

architecture weights_fifo_arch of weights_fifo_2k is

    -- AXI4-Stream Data FIFO: PC_WIDTH-bit TDATA + TLAST, common clock,
    -- active-low aresetn. One config for both weight and index PCs.
    component axis_data_fifo_pc is
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
    end component axis_data_fifo_pc;

    signal w_tvalid    : std_logic_vector(W_PCS-1 downto 0);
    signal w_tlast     : std_logic_vector(W_PCS-1 downto 0);
    signal ind_tvalid  : std_logic_vector(IND_PCS-1 downto 0);

    signal all_valid  : std_logic;
    signal pop        : std_logic;
    signal ind_concat : std_logic_vector(IND_PCS*PC_WIDTH-1 downto 0);

begin

    -- Barrier: an aligned beat exists only when every weight AND index PC has data.
    all_valid <= '1' when (w_tvalid   = (w_tvalid'range   => '1')) and
                          (ind_tvalid = (ind_tvalid'range => '1'))
                 else '0';

    ready <= all_valid;
    pop   <= rd_en and all_valid;   -- pop every PC together, never partially

    -- end of weight matrix: the last weight beat is being popped (all weight PCs
    -- carry tlast on the same beat). Pre-pipeline marker -- the top aligns it.
    tlast_out <= '1' when (pop = '1' and w_tlast = (w_tlast'range => '1')) else '0';

    -- ---- weight PC FIFOs ----
    W_FIFOS : for i in 0 to W_PCS-1 generate
        W_FIFO_INST : axis_data_fifo_pc
        port map(
            s_axis_aclk    => clk,
            s_axis_aresetn => resetn,
            s_axis_tvalid  => s_axis_w_tvalid(i),
            s_axis_tready  => s_axis_w_tready(i),
            s_axis_tdata   => s_axis_w_tdata((i+1)*PC_WIDTH-1 downto i*PC_WIDTH),
            s_axis_tlast   => s_axis_w_tlast(i),
            m_axis_tvalid  => w_tvalid(i),
            m_axis_tready  => pop,
            m_axis_tdata   => W_out((i+1)*PC_WIDTH-1 downto i*PC_WIDTH),
            m_axis_tlast   => w_tlast(i)
        );
    end generate W_FIFOS;

    -- ---- index PC FIFOs ----
    IND_FIFOS : for i in 0 to IND_PCS-1 generate
        IND_FIFO_INST : axis_data_fifo_pc
        port map(
            s_axis_aclk    => clk,
            s_axis_aresetn => resetn,
            s_axis_tvalid  => s_axis_ind_tvalid(i),
            s_axis_tready  => s_axis_ind_tready(i),
            s_axis_tdata   => s_axis_ind_tdata((i+1)*PC_WIDTH-1 downto i*PC_WIDTH),
            s_axis_tlast   => '0',                 -- index tlast unused (end-of-calc is on weights)
            m_axis_tvalid  => ind_tvalid(i),
            m_axis_tready  => pop,
            m_axis_tdata   => ind_concat((i+1)*PC_WIDTH-1 downto i*PC_WIDTH),
            m_axis_tlast   => open
        );
    end generate IND_FIFOS;

    -- Split the joined index word: low IND_BITS are the indices; the 2 sparsity
    -- bits for this batch ride in PC2's padding just above them.
    indices_out  <= ind_concat(IND_BITS-1 downto 0);          -- [639:0]
    Sparsity_out <= ind_concat(IND_BITS+1 downto IND_BITS);   -- [641:640]

end architecture weights_fifo_arch;
