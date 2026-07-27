library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
--
-- Description:
-- DENSE variant of c_core -- 8 independent c_block_dense units, each owning one
-- output row. As in the sparse design there is NO reduction tree: blocks are
-- never summed, each accumulates its own row and they all flush together.
--
-- The one structural change vs the sparse core is the activation window.
--
--   sparse : A_internal is a 32-element window HELD (frozen) by sparsity_lock
--            for 32/M cycles, and each block re-gathers 2 elements out of it
--            every cycle through its own 2 x 32:1 index mux.
--   dense  : every block is at the SAME position in its row, so at beat c of a
--            window all 64 blocks want elements 2c and 2c+1. A_internal becomes
--            a SHIFT REGISTER: loaded on win_lock='0', shifted right by one
--            element pair on every subsequent valid beat. All blocks tap the low
--            32 bits. 128 gather muxes -> one shift. That delta is the area cost
--            of sparsity support, and is the point of this baseline.
--
-- The shift is driven by exactly the signals that loaded the window in the
-- sparse core (valid_in / win_lock), so beat c of the freeze automatically pairs
-- with elements 2c/2c+1 -- there is no counter and no phase to get wrong.
--
-- Window = A_IDX elements, 2 consumed per cycle -> A_IDX/2 = 16 beats per window
-- (the fixed dense freeze; the top module holds win_lock for that long).
--
-- Throughput: 1 beat/cycle, 8 rows flushed per window on tlast_in.
-- ----------------------------------------------------------------------------

entity c_core_dense is
    generic(
        EL_SIZE     : integer := 16;    -- Bit size of each element
        W_IDX       : integer := 16;    -- Number of matrix elements
        A_IDX       : integer := 32;    -- Number of vector elements (the window)
        BLOCKS_NUM  : integer := 8      -- Number of c blocks (2 elements each)
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        W_row       : in std_logic_vector ((W_IDX*EL_SIZE)-1 downto 0);
        A_vector    : in std_logic_vector ((A_IDX*EL_SIZE)-1 downto 0);

        win_lock    : in std_logic;     -- '0' = load a new window, '1' = advance within it

        valid_in    : in std_logic;
        tlast_in    : in std_logic;

        Cout        : out std_logic_vector ((EL_SIZE*BLOCKS_NUM)-1 downto 0);
        Cvalid      : out std_logic;
        Ctlast      : out std_logic
    );
end entity c_core_dense;


architecture c_core_dense_arch of c_core_dense is

    component c_block_dense is
    generic(
        EL_SIZE : integer := 16;    -- Bit size of each element
        W_IDX   : integer := 2;     -- Number of matrix elements
        A_IDX   : integer := 2      -- Number of vector elements (the pre-selected pair)
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        W_in        : in std_logic_vector ((EL_SIZE*W_IDX)-1 downto 0);     -- Matrix Input
        A_in        : in std_logic_vector ((EL_SIZE*A_IDX)-1 downto 0);     -- Vector Input (2 elements)

        valid       : in std_logic;
        tlast_in    : in std_logic;

        Cout        : out std_logic_vector (EL_SIZE-1 downto 0);            -- Calculated Output Element
        Cvalid      : out std_logic;
        Ctlast      : out std_logic
    );
    end component c_block_dense;

    -- One block consumes 2 elements per beat -> the window advances by this many
    -- bits each cycle, and a window lasts A_IDX/2 beats.
    constant PAIR_BITS : integer := 2*EL_SIZE;

    signal W_internal       : std_logic_vector ((EL_SIZE*W_IDX)-1 downto 0);
    signal A_internal       : std_logic_vector ((EL_SIZE*A_IDX)-1 downto 0);

    signal valid_internal   : std_logic;
    signal tlast_internal   : std_logic;

begin

    MAIN_PROC : process(clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then

                W_internal <= (others => '0');
                A_internal <= (others => '0');

                valid_internal <= '0';
                tlast_internal <= '0';

            else

                if valid_in = '1' then

                    -- Activation window shift register (replaces the sparse frozen
                    -- window + per-block gather). Little-endian: the pair in use is
                    -- always at the LSB end, so shifting RIGHT by one pair advances
                    -- every block from elements (2c,2c+1) to (2c+2,2c+3) together.
                    if win_lock = '0' then
                        A_internal <= A_vector;                                                  -- new window: pair 0 at the LSB
                    else
                        A_internal <= std_logic_vector(shift_right(unsigned(A_internal), PAIR_BITS));
                    end if;

                    valid_internal <= '1';
                    tlast_internal <= tlast_in;

                    W_internal <= W_row;

                else

                    valid_internal <= '0';
                    tlast_internal <= '0';

                end if;
            end if;
        end if;
    end process MAIN_PROC;

    C_CORE : for i in 0 to BLOCKS_NUM-1 generate
        C_BLOCK_FIRST : if i = 0 generate
            C_BLOCK_INST_FIRST : c_block_dense
            generic map (
                EL_SIZE => EL_SIZE,
                W_IDX => 2,
                A_IDX => 2
            )
            port map (
                clk => clk,
                resetn => resetn,

                W_in => W_internal(((i+1)*2*EL_SIZE)-1 downto (i*2*EL_SIZE)),
                A_in => A_internal(PAIR_BITS-1 downto 0),   -- shared pair: same for every block

                valid => valid_internal,
                tlast_in => tlast_internal,

                Cout => Cout(((i+1)*EL_SIZE)-1 downto (i*EL_SIZE)),
                Cvalid => Cvalid,
                Ctlast => Ctlast
            );
        end generate C_BLOCK_FIRST;

        C_BLOCK_REST : if i /= 0 generate
            C_BLOCK_INST_REST : c_block_dense
                generic map (
                    EL_SIZE => EL_SIZE,
                    W_IDX => 2,
                    A_IDX => 2
                )
                port map (
                    clk => clk,
                    resetn => resetn,

                    W_in => W_internal(((i+1)*2*EL_SIZE)-1 downto (i*2*EL_SIZE)),
                    A_in => A_internal(PAIR_BITS-1 downto 0),   -- shared pair: same for every block

                    valid => valid_internal,
                    tlast_in => tlast_internal,

                    Cout => Cout(((i+1)*EL_SIZE)-1 downto (i*EL_SIZE)),
                    Cvalid => open,
                    Ctlast => open
                );
        end generate C_BLOCK_REST;
    end generate C_CORE;

end architecture c_core_dense_arch;
