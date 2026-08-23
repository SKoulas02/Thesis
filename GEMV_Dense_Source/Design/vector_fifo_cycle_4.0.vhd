library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
--
-- Description:
-- Activation replay (recirculation) buffer for the GEMV 4.0 datapath.
-- Holds the activation vector on-chip so it can be reused across many weight
-- rows after the HBM activation channel has stopped.
--
-- Two phases, selected by cycle_en:
--   LOAD   (cycle_en='0'): din = A_vector & tlast_in, written on valid_in.
--                          The full vector is streamed in once (rd_en held '0').
--   REPLAY (cycle_en='1'): din = dout, written on rd_en. Each rd_en pops the
--                          head AND re-enqueues it, so the vector recirculates
--                          in order indefinitely without re-reading HBM.
--
-- tlast rides at bit 0 of the FIFO word; in replay it comes around once per lap,
-- marking the vector boundary. Because wr_en = rd_en during replay, dropping
-- rd_en (global gate low) freezes recirculation with occupancy held, keeping the
-- activations aligned with the stalled weight stream.
--
-- Requirements (set in the IP catalog / XPM):
--   * fifo_gen_vector_cycle : (PC_WIDTH*A_PCS+1)=513-bit, FIRST-WORD-FALL-THROUGH.
--     FWFT is mandatory: the write-back samples dout on the same cycle as the
--     pop, so dout must already be the head (standard read mode re-enqueues the
--     wrong word and corrupts the vector after one lap).
--   * Size ABOVE the vector (e.g. 128 deep for a 64-beat vector) so it is never
--     full during recirc -- a simultaneous write-back at 'full' would be dropped.
-- Sequencing: hold rd_en='0' during LOAD; switch cycle_en 0->1 only after the
-- whole vector is loaded.
-- ----------------------------------------------------------------------------

entity vector_cycle_512 is
    generic(
        PC_WIDTH : integer := 256;      -- HBM pseudo-channel data width
        A_PCS    : integer := 2         -- activation PCs (2*256 = 512 bits)
    );
    port(
        clk     : in std_logic;
        resetn  : in std_logic;

        -- ---- write side: from HBM, one strobe + rlast per pseudo-channel ----
        A_vector : in  std_logic_vector(A_PCS*PC_WIDTH-1 downto 0);
        tlast_in : in  std_logic;
        valid_in : in  std_logic;
        
        rd_en        : in  std_logic;
        cycle_en     : in  std_logic;

        -- Empty the buffer so the NEXT calculation loads a fresh vector.
        -- Without this the engine is single-shot per programming: the top
        -- correctly returns to load mode at end-of-calculation, but the old
        -- vector is still sitting in this FIFO, so the new one is appended
        -- behind it and the head read back is the STALE one. Simulation never
        -- caught it (one calculation per run, always from reset); hardware did.
        -- Defaulted so existing instantiations elaborate unchanged.
        flush        : in  std_logic := '0';

        ready        : out std_logic;
        A_vector_out : out std_logic_vector(A_PCS*PC_WIDTH-1 downto 0);
        tlast_out    : out std_logic
    );
end entity vector_cycle_512;

architecture vector_cycle_arch of vector_cycle_512 is

    component fifo_gen_vector_cycle is
        port(
            clk   : in  std_logic;
            srst  : in  std_logic;
            din   : in  std_logic_vector(PC_WIDTH*A_PCS downto 0);
            wr_en : in  std_logic;
            rd_en : in  std_logic;
            dout  : out std_logic_vector(PC_WIDTH*A_PCS downto 0);
            empty : out std_logic
        );
    end component fifo_gen_vector_cycle;

    signal reset : std_logic;

    signal a_dout  : std_logic_vector (PC_WIDTH*A_PCS downto 0);
    signal a_in    : std_logic_vector (PC_WIDTH*A_PCS downto 0);
    signal empty   : std_logic;
    signal wr_en   : std_logic;
    signal wr_en_g : std_logic;   -- flush-gated
    signal rd_en_g : std_logic;   -- flush-gated

begin

    -- Synchronous reset: power-on reset OR an end-of-calculation flush.
    -- fifo_generator requires srst held for several clock cycles with wr_en and
    -- rd_en LOW throughout, so the top drives 'flush' as a multi-cycle pulse and
    -- the gating below guarantees the enables are quiet regardless of what the
    -- control happens to be driving.
    reset <= (not resetn) or flush;
    ready <= (not empty) and (not flush);   -- never advertise data mid-flush

    a_in            <= A_vector & tlast_in when cycle_en = '0' else a_dout;
    wr_en           <= valid_in when cycle_en = '0' else rd_en;
    A_vector_out    <= a_dout(PC_WIDTH*A_PCS downto 1);
    tlast_out       <= a_dout(0);

    wr_en_g <= wr_en and (not flush);
    rd_en_g <= rd_en and (not flush);

    A_FIFO_INST : fifo_gen_vector_cycle
    port map(
        clk   => clk,
        srst  => reset,
        din   => a_in,
        wr_en => wr_en_g,
        rd_en => rd_en_g,
        dout  => a_dout,
        empty => empty
    );

end architecture vector_cycle_arch;
