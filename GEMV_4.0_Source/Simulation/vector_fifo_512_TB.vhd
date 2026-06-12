library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity vector_fifo_512_TB is
end entity vector_fifo_512_TB;

architecture vector_fifo_512_TB_arch of vector_fifo_512_TB is

    -- Component declaration
    component vector_fifo_512 is
    port(
        clk          : in  std_logic;
        resetn       : in  std_logic;

        A_vector_in  : in  std_logic_vector(511 downto 0);
        A_valid_in   : in  std_logic;
        tlast_in     : in  std_logic;

        rd_en        : in  std_logic;

        A_vector_out : out std_logic_vector(511 downto 0);
        A_valid_out  : out std_logic;

        tlast_out    : out std_logic;
        empty        : out std_logic
    );
    end component vector_fifo_512;

    -- Clock and reset
    constant CLK_PERIOD : time := 10 ns;
    signal clk    : std_logic := '0';
    signal resetn : std_logic := '0';

    -- DUT signals (one per port, using concrete resolved types)
    signal A_vector_in  : std_logic_vector(511 downto 0) := (others => '0');
    signal A_valid_in   : std_logic := '0';
    signal tlast_in     : std_logic := '0';

    signal rd_en        : std_logic := '0';

    signal A_vector_out : std_logic_vector(511 downto 0) := (others => '0');
    signal A_valid_out  : std_logic := '0';

    signal tlast_out    : std_logic := '0';
    signal empty        : std_logic := '0';

begin

    -- Clock generation
    CLK_GEN : process
    begin
        clk <= '1';
        wait for CLK_PERIOD / 2;
        clk <= '0';
        wait for CLK_PERIOD / 2;
    end process CLK_GEN;

    -- DUT instantiation
    UUT : vector_fifo_512
    port map(
        clk          => clk,
        resetn       => resetn,

        A_vector_in  => A_vector_in,
        A_valid_in   => A_valid_in,
        tlast_in     => tlast_in,

        rd_en        => rd_en,

        A_vector_out => A_vector_out,
        A_valid_out  => A_valid_out,

        tlast_out    => tlast_out,
        empty        => empty
    );

    -- Stimulus
    STIM : process
    begin
        -- Apply reset, idle all inputs
        resetn      <= '0';
        A_valid_in  <= '0';
        tlast_in    <= '0';
        rd_en       <= '0';
        A_vector_in <= (others => '0');
        wait for CLK_PERIOD * 5;
        resetn <= '1';
        wait for CLK_PERIOD * 2;

        -- ---- Write three vectors into the FIFO ----
        -- Vector 1 (low bits = 1 for easy identification)
        A_vector_in <= std_logic_vector(to_unsigned(1, 512));
        tlast_in    <= '0';
        A_valid_in  <= '1';
        wait for CLK_PERIOD;

        -- Vector 2
        A_vector_in <= std_logic_vector(to_unsigned(2, 512));
        tlast_in    <= '0';
        wait for CLK_PERIOD;

        -- Vector 3 (last beat of the sequence)
        A_vector_in <= std_logic_vector(to_unsigned(3, 512));
        tlast_in    <= '1';
        wait for CLK_PERIOD;

        -- Stop writing
        A_valid_in  <= '0';
        tlast_in    <= '0';
        A_vector_in <= (others => '0');
        wait for CLK_PERIOD * 4;

        -- ---- Read the three vectors back out ----
        rd_en <= '1';
        wait for CLK_PERIOD*2;
        rd_en <= '0';
        wait for CLK_PERIOD * 5;
        rd_en <= '1';
        wait for CLK_PERIOD;
        rd_en <= '0';
        

        wait for CLK_PERIOD * 10;
        wait;
    end process STIM;

end architecture vector_fifo_512_TB_arch;
