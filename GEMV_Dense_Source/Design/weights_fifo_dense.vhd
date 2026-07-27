library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
--
-- Description:
-- DENSE variant of weights_fifo_2k. HBM ingress + alignment for the weight
-- stream only: the 3 index PCs (640 index bits + the 2 sparsity bits that rode
-- in the PC2 padding) are removed, because dense needs neither -- every weight
-- is positional and the freeze length is a constant.
--
--   sparse : 11 PCs joined (8 weights + 3 indices), 2048 + 640 bits per beat
--   dense  :  8 PCs joined (weights only),          2048 bits per beat
--
-- That is the whole HBM saving of the dense side: 17 PCs -> 14, and 2688 bits
-- per beat -> 2048 (a 31% per-beat reduction; see the data-movement table in
-- the thesis). Structure is otherwise identical to the sparse module: one
-- AXI4-Stream Data FIFO per PC (native TREADY backpressure to the read engine)
-- and a barrier JOIN on the m_axis side that presents one aligned word only when
-- every PC FIFO has a beat -- all PCs popped together (shared tready) so there
-- is no reordering.
--
-- The weight stream carries TLAST = end of the weight matrix. tlast_out pulses
-- on the beat the LAST weight beat is popped (= no more work). This is a
-- pre-pipeline marker: the TOP aligns it with the drained output (the activation
-- lap tlast / Ctlast path) to set the final output TLAST -- it must NOT be used
-- raw as "done", since the last result emerges pipeline-latency later.
--
-- IP configuration (AXI4-Stream Data FIFO, one per PC -- "axis_data_fifo_pc",
-- the SAME IP the sparse design uses, so the comparison is not perturbed):
--   * TDATA width = PC_WIDTH (256 b = 32 bytes), TLAST enabled, no TKEEP/TUSER.
--   * Common clock (synchronous), active-low aresetn, NON-packet mode.
--   * Depth 512 (Memory type Auto -> BRAM): weights are consumed EVERY cycle,
--     so size to ride one HBM read refresh (~110-160 cyc) + skew.
--   * No prog_full needed: s_axis_tready backpressures the read engine natively;
--     the join uses the empty side (m_axis_tvalid) via 'ready'.
--
-- PC mapping (host packs HBM this way):
--   weights : PC k -> W_out[(k+1)*256-1 : k*256]  (= core k's 16 weights)
-- ----------------------------------------------------------------------------

entity weights_fifo_dense is
    generic(
        PC_WIDTH   : integer := 256;    -- HBM pseudo-channel data width
        W_PCS      : integer := 8       -- weight PCs (8*256 = 2048 bits)
    );
    port(
        clk     : in std_logic;
        resetn  : in std_logic;         -- active-low (tied to AXIS aresetn)

        -- ---- write side: AXI-Stream slave, one stream per PC (from read engine) ----
        s_axis_w_tdata    : in  std_logic_vector(W_PCS*PC_WIDTH-1 downto 0);
        s_axis_w_tvalid   : in  std_logic_vector(W_PCS-1 downto 0);
        s_axis_w_tready   : out std_logic_vector(W_PCS-1 downto 0);
        s_axis_w_tlast    : in  std_logic_vector(W_PCS-1 downto 0);   -- end of weight matrix

        -- ---- read side: one aligned word (join handshake) ----
        rd_en        : in  std_logic;                                     -- consumer pop
        ready        : out std_logic;                                     -- all PCs have a beat
        tlast_out    : out std_logic;                                     -- last weight beat popped
        W_out        : out std_logic_vector(W_PCS*PC_WIDTH-1 downto 0)    -- 2048
    );
end entity weights_fifo_dense;

architecture weights_fifo_dense_arch of weights_fifo_dense is

    -- AXI4-Stream Data FIFO: PC_WIDTH-bit TDATA + TLAST, common clock,
    -- active-low aresetn.
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

    signal all_valid  : std_logic;
    signal pop        : std_logic;

begin

    -- Barrier: an aligned beat exists only when every weight PC has data.
    all_valid <= '1' when (w_tvalid = (w_tvalid'range => '1')) else '0';

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

end architecture weights_fifo_dense_arch;
