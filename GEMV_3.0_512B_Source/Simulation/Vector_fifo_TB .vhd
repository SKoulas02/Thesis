library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Testbench for vector_fifo_64 (Component Instantiation)
-- ----------------------------------------------------------------------------

entity tb_vector_fifo_64 is
end entity tb_vector_fifo_64;

architecture behavior of tb_vector_fifo_64 is

    -- 1. Match the Generics from the DUT (Device Under Test)
    constant EL_SIZE : integer := 16;
    constant BUS_EL  : integer := 4;

    -- 2. Helper constant to calculate dynamic port widths
    constant VEC_WIDTH : integer := EL_SIZE * BUS_EL;

    -- 3. Component Declaration
    component vector_fifo_64 is
        generic(
            EL_SIZE     : integer := 16;
            BUS_EL      : integer := 4
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;

            B_vector_in : in std_logic_vector((EL_SIZE*BUS_EL)-1 downto 0);
            B_valid_in  : in std_logic;
            tlast_in    : in std_logic;

            rd_en       : in std_logic;

            B_vector_out    : out std_logic_vector ((EL_SIZE*BUS_EL)-1 downto 0);
            B_valid_out     : out std_logic;

            tlast_out       : out std_logic;
            empty           : out std_logic
        );
    end component vector_fifo_64;

    -- 4. Signal Declarations
    signal clk          : std_logic := '0';
    signal resetn       : std_logic := '0';
    
    -- Inputs
    signal B_vector_in  : std_logic_vector(VEC_WIDTH - 1 downto 0) := (others => '0');
    signal B_valid_in   : std_logic := '0';
    signal tlast_in     : std_logic := '0';
    signal rd_en        : std_logic := '0';
    
    -- Outputs
    signal B_vector_out : std_logic_vector(VEC_WIDTH - 1 downto 0);
    signal B_valid_out  : std_logic;
    signal tlast_out    : std_logic;
    signal empty        : std_logic;

    -- Clock period definition
    constant CLK_PERIOD : time := 10 ns;

begin

    -- ------------------------------------------------------------------------
    -- Instantiate the Unit Under Test (UUT) using the Component
    -- ------------------------------------------------------------------------
    uut: vector_fifo_64
        generic map (
            EL_SIZE  => EL_SIZE,
            BUS_EL   => BUS_EL
        )
        port map (
            clk          => clk,
            resetn       => resetn,
            B_vector_in  => B_vector_in,
            B_valid_in   => B_valid_in,
            tlast_in     => tlast_in,
            rd_en        => rd_en,
            B_vector_out => B_vector_out,
            B_valid_out  => B_valid_out,
            tlast_out    => tlast_out,
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
        resetn <= '0';
        wait for 100 ns;
        
        -- Release reset and wait a few clock cycles
        resetn <= '1';
        wait for CLK_PERIOD * 5;

        -- Step 2: Write Data into the FIFO
        -- Write Word 1 (No tlast)
        B_valid_in <= '1';
        B_vector_in <= (others => '1'); -- Fill with all 1s as a test pattern
        tlast_in <= '0';
        wait for CLK_PERIOD;

        -- Write Word 2 (No tlast)
        B_vector_in <= x"AAAA" & (VEC_WIDTH - 17 downto 0 => '0'); -- specific hex pattern
        tlast_in <= '0';
        wait for CLK_PERIOD;

        -- Write Word 3 (With tlast asserted to indicate end of vector)
        B_vector_in <= x"BBBB" & (VEC_WIDTH - 17 downto 0 => '0');
        tlast_in <= '1';
        wait for CLK_PERIOD;

        -- Deassert write signals
        B_valid_in <= '0';
        B_vector_in <= (others => '0');
        tlast_in <= '0';
        wait for CLK_PERIOD * 5;

        -- Step 3: Read Data from the FIFO
        -- Read Word 1
        rd_en <= '1';
        wait for CLK_PERIOD;

        -- Read Word 2
        wait for CLK_PERIOD;

        -- Read Word 3 (Observe tlast_out pulse high during this read)
        wait for CLK_PERIOD;

        -- Deassert read enable
        rd_en <= '0';
        wait for CLK_PERIOD * 5;
        
        -- End Simulation
        report "Simulation completed successfully." severity note;
        wait;
        
    end process;

end architecture behavior;