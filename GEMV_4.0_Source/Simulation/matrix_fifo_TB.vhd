library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Testbench for matrix_fifo_64 (Component Instantiation)
-- ----------------------------------------------------------------------------

entity tb_matrix_fifo_64 is
end entity tb_matrix_fifo_64;

architecture behavior of tb_matrix_fifo_64 is

    -- 1. Match the Generics from the DUT (Device Under Test)
    constant EL_SIZE : integer := 16;
    constant BUS_EL  : integer := 4;
    constant A_IDX   : integer := 2;
    constant B_IDX   : integer := 4;
    constant IND_NUM : integer := 4;
    constant A_ROWS  : integer := 8;

    -- 2. Helper constants to calculate dynamic port widths cleanly
    constant A_IN_WIDTH       : integer := BUS_EL * EL_SIZE;                          -- 64
    constant A_OUT_WIDTH      : integer := A_ROWS * BUS_EL * EL_SIZE / A_IDX;         -- 256
    constant INDICES_IN_WIDTH : integer := 32;
    constant IND_OUT_WIDTH    : integer := A_ROWS * IND_NUM * BUS_EL / B_IDX;         -- 32

    -- 3. Component Declaration
    component matrix_fifo_64 is
        generic(
            EL_SIZE     : integer := 16;
            BUS_EL      : integer := 4;
            A_IDX       : integer := 2;
            B_IDX       : integer := 4;
            IND_NUM     : integer := 4;
            A_ROWS      : integer := 8
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;

            A_in        : in std_logic_vector (BUS_EL*EL_SIZE-1 downto 0);
            A_valid_in  : in std_logic;

            indices     : in std_logic_vector (31 downto 0);
            ind_valid   : in std_logic;

            rd_en       : in std_logic_vector (A_ROWS-1 downto 0);

            A_out           : out std_logic_vector ((A_ROWS*BUS_EL*EL_SIZE/A_IDX)-1 downto 0);
            indices_out     : out std_logic_vector ((A_ROWS * IND_NUM * BUS_EL/B_IDX) - 1 downto 0);
            empty           : out std_logic_vector (A_ROWS-1 downto 0)
        );
    end component matrix_fifo_64;

    -- 4. Signal Declarations
    signal clk          : std_logic := '0';
    signal resetn       : std_logic := '0';

    -- Inputs
    signal A_in         : std_logic_vector(A_IN_WIDTH - 1 downto 0)       := (others => '0');
    signal A_valid_in   : std_logic                                       := '0';
    signal indices      : std_logic_vector(INDICES_IN_WIDTH - 1 downto 0) := (others => '0');
    signal ind_valid    : std_logic                                       := '0';
    signal rd_en        : std_logic_vector(A_ROWS - 1 downto 0)           := (others => '0');

    -- Outputs
    signal A_out        : std_logic_vector(A_OUT_WIDTH - 1 downto 0);
    signal indices_out  : std_logic_vector(IND_OUT_WIDTH - 1 downto 0);
    signal empty        : std_logic_vector(A_ROWS - 1 downto 0);

    -- Clock period definition
    constant CLK_PERIOD : time := 10 ns;

begin

    -- ------------------------------------------------------------------------
    -- Instantiate the Unit Under Test (UUT) using the Component
    -- ------------------------------------------------------------------------
    uut: matrix_fifo_64
        generic map (
            EL_SIZE => EL_SIZE,
            BUS_EL  => BUS_EL,
            A_IDX   => A_IDX,
            B_IDX   => B_IDX,
            IND_NUM => IND_NUM,
            A_ROWS  => A_ROWS
        )
        port map (
            clk          => clk,
            resetn       => resetn,
            A_in         => A_in,
            A_valid_in   => A_valid_in,
            indices      => indices,
            ind_valid    => ind_valid,
            rd_en        => rd_en,
            A_out        => A_out,
            indices_out  => indices_out,
            empty        => empty
        );

    -- ------------------------------------------------------------------------
    -- Clock Generation Process
    -- ------------------------------------------------------------------------
    clk_process : process
    begin
        clk <= '1';
        wait for CLK_PERIOD/2;
        clk <= '0';
        wait for CLK_PERIOD/2;
    end process;

    -- ------------------------------------------------------------------------
    -- Stimulus Process
    -- ------------------------------------------------------------------------
    stim_proc: process
    begin
        -- Step 1: Initial Reset
        resetn     <= '0';
        A_valid_in <= '0';
        ind_valid  <= '0';
        rd_en      <= (others => '0');
        wait for 100 ns;

        -- Release reset and wait a few clock cycles
        resetn <= '1';
        wait for CLK_PERIOD * 5;

        -- --------------------------------------------------------------------
        -- Step 2: Write A_ROWS data words to exercise the round-robin write
        --         Each write targets the next row FIFO (1 row per cycle).
        --         Use a recognizable pattern: word i = {row tag, i, i, i}
        -- --------------------------------------------------------------------
        A_valid_in <= '1';
        for i in 0 to A_ROWS-1 loop
            -- 4 x 16-bit elements: high element identifies the word index
            A_in <= std_logic_vector(to_unsigned(16#A000# + i, 16)) &
                    std_logic_vector(to_unsigned(16#0B00# + i, 16)) &
                    std_logic_vector(to_unsigned(16#0C00# + i, 16)) &
                    std_logic_vector(to_unsigned(16#0D00# + i, 16));
            wait for CLK_PERIOD;
        end loop;

        -- Write a second pass so each row gets a second element queued up
        for i in 0 to A_ROWS-1 loop
            A_in <= std_logic_vector(to_unsigned(16#E000# + i, 16)) &
                    std_logic_vector(to_unsigned(16#0F00# + i, 16)) &
                    std_logic_vector(to_unsigned(16#1000# + i, 16)) &
                    std_logic_vector(to_unsigned(16#1100# + i, 16));
            wait for CLK_PERIOD;
        end loop;

        A_valid_in <= '1';
        A_in       <= (others => '0');
        A_valid_in <= '0';
        wait for CLK_PERIOD * 2;

        -- --------------------------------------------------------------------
        -- Step 3: Write indices to exercise the round-robin index write
        --         IND_PACK = 4, so each ind_valid cycle writes 4 row FIFOs.
        --         Two cycles cover all A_ROWS=8 index FIFOs.
        -- --------------------------------------------------------------------
        ind_valid <= '1';
        indices   <= x"12345678";   -- distributed across rows 0..3
        wait for CLK_PERIOD;

        indices   <= x"55667788";   -- distributed across rows 4..7
        wait for CLK_PERIOD;

        -- Second pass to queue a second indices entry per row FIFO
        indices   <= x"99AABBCC";
        wait for CLK_PERIOD;

        indices   <= x"DDEEFF00";
        wait for CLK_PERIOD;

        ind_valid <= '0';
        indices   <= (others => '0');
        wait for CLK_PERIOD * 5;

        -- --------------------------------------------------------------------
        -- Step 4: Read from all rows simultaneously
        --         empty should fall and A_out / indices_out should present
        --         the first queued word per row.
        -- --------------------------------------------------------------------
        rd_en(0) <= '1';
        rd_en(1) <= '1';
        wait for CLK_PERIOD*2;
        rd_en(0) <= '0';
        rd_en(1) <= '0';
        wait for CLK_PERIOD;     -- second read cycle (drains second entries)

        rd_en <= (others => '0');
        wait for CLK_PERIOD * 5;

        -- --------------------------------------------------------------------
        -- Step 5: Selective read - verify per-row rd_en independence
        --         Refill a few rows then read only a subset.
        -- --------------------------------------------------------------------
        A_valid_in <= '1';
        for i in 0 to A_ROWS-1 loop
            A_in <= std_logic_vector(to_unsigned(16#2200# + i, 16)) &
                    std_logic_vector(to_unsigned(16#3300# + i, 16)) &
                    std_logic_vector(to_unsigned(16#4400# + i, 16)) &
                    std_logic_vector(to_unsigned(16#5500# + i, 16));
            wait for CLK_PERIOD;
        end loop;
        A_valid_in <= '0';
        A_in       <= (others => '0');
        wait for CLK_PERIOD * 2;

        -- Read only rows 0, 3, and 7
        rd_en      <= (others => '0');
        rd_en(0)   <= '1';
        -- rd_en(3)   <= '1';
        -- rd_en(7)   <= '1';
        wait for CLK_PERIOD;

        rd_en <= (others => '0');
        wait for CLK_PERIOD * 5;

        -- --------------------------------------------------------------------
        -- Step 6: Reset mid-operation to confirm round-robin pointers reload
        -- --------------------------------------------------------------------
        resetn <= '0';
        wait for CLK_PERIOD * 3;
        resetn <= '1';
        wait for CLK_PERIOD * 3;

        -- One write + one read after reset to confirm recovery
        A_valid_in <= '1';
        A_in       <= x"DEADBEEFCAFEBABE";
        wait for CLK_PERIOD;
        A_valid_in <= '0';
        A_in       <= (others => '0');
        wait for CLK_PERIOD * 2;

        rd_en(0) <= '1';
        wait for CLK_PERIOD;
        rd_en    <= (others => '0');
        wait for CLK_PERIOD * 5;

        -- End Simulation
        report "Simulation completed successfully." severity note;
        wait;

    end process;

end architecture behavior;
