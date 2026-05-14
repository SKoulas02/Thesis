library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;

-- ----------------------------------------------------------------------------
-- Testbench for indices_fifo_1024 (Component Instantiation)
-- ----------------------------------------------------------------------------

entity tb_indices_fifo_1024 is
end entity tb_indices_fifo_1024;

architecture behavior of tb_indices_fifo_1024 is

    -- 1. Match the Generics from the DUT (Device Under Test)
    constant IND_NUM   : integer := 3;
    constant BUS_EL    : integer := 32;
    constant EL_SIZE   : integer := 16;
    constant A_IDX     : integer := 2;
    constant B_IDX     : integer := 4;
    constant A_ROWS    : integer := 128;

    -- 2. Helper constants to calculate dynamic port widths cleanly
    constant IND_ROWS      : integer := (2 ** integer(floor(log2(real((B_IDX*EL_SIZE)/IND_NUM)))));
    constant INDICES_WIDTH : integer := IND_ROWS * IND_NUM * BUS_EL / B_IDX;
    constant OUT_WIDTH     : integer := A_ROWS * IND_NUM * BUS_EL / B_IDX;

    -- 3. Component Declaration
    component indices_fifo_1024 is
        generic(
            IND_NUM     : integer := 3;
            BUS_EL      : integer := 32;
            EL_SIZE     : integer := 16;
            A_IDX       : integer := 2;
            B_IDX       : integer := 4;
            A_ROWS      : integer := 128
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;

            indices     : in std_logic_vector ( (2 ** integer(floor(log2(real((B_IDX*EL_SIZE)/IND_NUM))))) * IND_NUM*BUS_EL/B_IDX -1 downto 0);
            ind_valid   : in std_logic;

            rd_en       : in std_logic_vector (A_ROWS-1 downto 0);

            indices_out     : out std_logic_vector ((A_ROWS * IND_NUM * BUS_EL/B_IDX) - 1 downto 0);
            ind_valid_out   : out std_logic_vector (A_ROWS-1 downto 0);
            empty           : out std_logic_vector (A_ROWS-1 downto 0)
        );
    end component indices_fifo_1024;

    -- 4. Signal Declarations
    signal clk           : std_logic := '0';
    signal resetn        : std_logic := '0';
    
    -- Inputs
    signal indices       : std_logic_vector(INDICES_WIDTH - 1 downto 0) := (others => '0');
    signal ind_valid     : std_logic := '0';
    signal rd_en         : std_logic_vector(A_ROWS - 1 downto 0) := (others => '0');
    
    -- Outputs
    signal indices_out   : std_logic_vector(OUT_WIDTH - 1 downto 0);
    signal ind_valid_out : std_logic_vector(A_ROWS - 1 downto 0);
    signal empty         : std_logic_vector(A_ROWS - 1 downto 0);

    -- Clock period definition
    constant CLK_PERIOD : time := 10 ns;

begin

    -- ------------------------------------------------------------------------
    -- Instantiate the Unit Under Test (UUT) using the Component
    -- ------------------------------------------------------------------------
    uut: indices_fifo_1024
        generic map (
            IND_NUM  => IND_NUM,
            BUS_EL   => BUS_EL,
            EL_SIZE  => EL_SIZE,
            A_IDX    => A_IDX,
            B_IDX    => B_IDX,
            A_ROWS   => A_ROWS
        )
        port map (
            clk           => clk,
            resetn        => resetn,
            indices       => indices,
            ind_valid     => ind_valid,
            rd_en         => rd_en,
            indices_out   => indices_out,
            ind_valid_out => ind_valid_out,
            empty         => empty
        );

    -- ------------------------------------------------------------------------
    -- Clock Generation Process
    -- ------------------------------------------------------------------------
    clk_process : process
    begin
        clk <= '0';
        wait for CLK_PERIOD/2;
        clk <= '1';
        wait for CLK_PERIOD/2;
    end process;

    -- ------------------------------------------------------------------------
    -- Stimulus Process
    -- ------------------------------------------------------------------------
    stim_proc: process
    begin
        -- Step 1: Initial Reset
        resetn <= '0';
        wait for 100 ns;
        
        -- Release reset and wait a few clock cycles
        resetn <= '1';
        wait for CLK_PERIOD * 5;

        -- Step 2: Write Data into the FIFOs
        -- Assert the valid signal and apply a recognizable data pattern
        ind_valid <= '1';
        
        -- Populate the indices bus with a dummy pattern (byte-wise incremental)
        indices <= (others => '1');
        wait for CLK_PERIOD;

        wait for CLK_PERIOD;

        -- Deassert write signals
        ind_valid <= '0';
        indices <= (others => '0');
        wait for CLK_PERIOD * 10;

        -- Step 3: Read Data from the FIFOs
        -- Enable reading from a few specific rows to verify `indices_out` mapping
        rd_en(0)  <= '1';
        rd_en(1)  <= '1';
        rd_en(15) <= '1';
        rd_en(25) <= '1';
        wait for CLK_PERIOD;

        -- Deassert read enables
        rd_en <= (others => '0');
        wait for CLK_PERIOD * 5;
        
        -- End Simulation
        report "Simulation completed successfully." severity note;
        wait;
        
    end process;

end architecture behavior;