library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use work.matrix_array_pkg.all;

entity matrix_fifo_TB is
end entity matrix_fifo_TB;

architecture matrix_fifo_synthesis_TB of matrix_fifo_TB is

    component matrix_fifo is
        port(
            clk         : in std_logic;
            resetn      : in std_logic;

            A_in        : in std_logic_vector (8*16-1 downto 0); 
            A_valid_in  : in std_logic;

            rd_en       : in std_logic_vector (16-1 downto 0);
            
            
            --Temp
            write_en_out    : out std_logic_vector (16-1 downto 0);
            write_reg_out   : out std_logic_vector (16-1 downto 0);


            A_out           : out std_logic_vector ((16*64)-1 downto 0);
            A_valid_out     : out std_logic_vector (16-1 downto 0);
            empty           : out std_logic_vector (16-1 downto 0)
        );
    end component;

    constant CLK_PERIOD : time := 10 ns;

    signal clk          : std_logic := '0';
    signal resetn       : std_logic := '0';
    
    signal A_in         : std_logic_vector(8*16-1 downto 0) := (others => '0');
    signal A_valid_in   : std_logic := '0';
    signal rd_en        : std_logic_vector(16-1 downto 0) := (others => '0');
    
    signal A_out_flat   : std_logic_vector((16*64)-1 downto 0);
    signal A_out        : matrix_array(0 to 16-1);
    signal A_valid_out  : std_logic_vector(16-1 downto 0);
    signal empty        : std_logic_vector(16-1 downto 0);

    signal write_en     : std_logic_vector(16-1 downto 0);
    signal write_reg    : std_logic_vector(16-1 downto 0);

begin

    DUT: matrix_fifo
    port map(
        clk           => clk,
        resetn        => resetn,
        A_in          => A_in,
        A_valid_in    => A_valid_in,
        rd_en         => rd_en,
        A_out         => A_out_flat,
        A_valid_out   => A_valid_out,
        empty         => empty,

        write_en_out  => write_en,
        write_reg_out => write_reg
    );

    UNPACK_MATRIX : for i in 0 to 16-1 generate
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

end architecture matrix_fifo_synthesis_TB;