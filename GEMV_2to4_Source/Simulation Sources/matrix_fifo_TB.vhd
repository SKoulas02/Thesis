library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use work.matrix_array_pkg.all;

entity matrix_fifo_TB is
end entity matrix_fifo_TB;

architecture matrix_fifo_arch_TB of matrix_fifo_TB is

    component matrix_fifo is
        generic(
            EL_SIZE     : integer := 16;    -- Bits of each Element
            BUS_EL      : integer := 8;     -- Max elements on Bus
            A_IDX       : integer := 2;     -- Number of Matrix Elements (2to4)
            A_ROWS      : integer := 16     -- Number of Rows of Matrix
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;

            A_in        : in std_logic_vector (BUS_EL*EL_SIZE-1 downto 0); 
            A_valid_in  : in std_logic;

            rd_en       : in std_logic_vector (A_ROWS-1 downto 0);

            A_out           : out std_logic_vector ((A_ROWS*64)-1 downto 0);
            A_valid_out     : out std_logic_vector (A_ROWS-1 downto 0);
            empty           : out std_logic_vector (A_ROWS-1 downto 0)
        );
    end component;

    constant C_EL_SIZE  : integer := 16;
    constant C_BUS_EL   : integer := 8;
    constant C_A_IDX    : integer := 2;
    constant C_A_ROWS   : integer := 16;
    constant CLK_PERIOD : time := 10 ns;

    signal clk          : std_logic := '0';
    signal resetn       : std_logic := '0';
    
    signal A_in         : std_logic_vector(C_BUS_EL*C_EL_SIZE-1 downto 0) := (others => '0');
    signal A_valid_in   : std_logic := '0';
    signal rd_en        : std_logic_vector(C_A_ROWS-1 downto 0) := (others => '0');
    
    signal A_out_flat   : std_logic_vector((C_A_ROWS*64)-1 downto 0);
    signal A_out        : matrix_array(0 to C_A_ROWS-1);
    signal A_valid_out  : std_logic_vector(C_A_ROWS-1 downto 0);
    signal empty        : std_logic_vector(C_A_ROWS-1 downto 0);

begin

    DUT: matrix_fifo
    generic map(
        EL_SIZE => C_EL_SIZE,
        BUS_EL  => C_BUS_EL,
        A_IDX   => C_A_IDX,
        A_ROWS  => C_A_ROWS
    )
    port map(
        clk           => clk,
        resetn        => resetn,
        A_in          => A_in,
        A_valid_in    => A_valid_in,
        rd_en         => rd_en,
        A_out         => A_out_flat,
        A_valid_out   => A_valid_out,
        empty         => empty
    );

    UNPACK_MATRIX : for i in 0 to C_A_ROWS-1 generate
        A_out(i) <= A_out_flat(((i+1) * 64) - 1 downto i * 64);
    end generate;

    CLK_PROC : process
    begin
        clk <= '0';
        wait for CLK_PERIOD/2;
        clk <= '1';
        wait for CLK_PERIOD/2;
    end process;

    STIMULUS : process
    begin       
        resetn <= '0';
        wait for CLK_PERIOD * 5; 
        resetn <= '1';
        wait for CLK_PERIOD * 5;

        A_in <= x"1111_1111_1111_1111_1111_1111_1111_1111";
        A_valid_in <= '1';
        wait for CLK_PERIOD;
        
        A_in <= x"2222_2222_2222_2222_2222_2222_2222_2222";
        wait for CLK_PERIOD;

        A_in <= x"3333_3333_3333_3333_3333_3333_3333_3333";
        wait for CLK_PERIOD;

        A_in <= x"4444_4444_4444_4444_4444_4444_4444_4444";
        wait for CLK_PERIOD;
        
        A_valid_in <= '0';
        A_in <= (others => '0');
        
        wait for CLK_PERIOD * 5;
        
        rd_en <= x"FFFF";
        wait for CLK_PERIOD;
        
        rd_en <= (others => '0');
        
        wait;
    end process;

end architecture matrix_fifo_arch_TB;