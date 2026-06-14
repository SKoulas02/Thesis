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

begin

    reset <= not resetn;
    ready <= not empty;


    a_in            <= A_vector & tlast_in when cycle_en = '0' else a_dout;
    wr_en           <= valid_in when cycle_en = '0' else rd_en;
    A_vector_out    <= a_dout(PC_WIDTH*A_PCS downto 1);
    tlast_out       <= a_dout(0);

    A_FIFO_INST : fifo_gen_vector_cycle
    port map(
        clk   => clk,
        srst  => reset,
        din   => a_in,
        wr_en => wr_en,
        rd_en => rd_en,
        dout  => a_dout,
        empty => empty
    );

end architecture vector_cycle_arch;
