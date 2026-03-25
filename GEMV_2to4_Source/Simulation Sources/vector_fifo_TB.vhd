library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity vector_fifo_TB is
end entity vector_fifo_TB;

architecture vector_fifo_arch_TB of vector_fifo_TB is

    component vector_fifo is
        generic(
            EL_SIZE     : integer := 16;
            BUS_EL      : integer := 8
        );
        port(
            clk             : in std_logic;
            resetn          : in std_logic;
            B_vector_in     : in std_logic_vector((16*8)-1 downto 0);
            B_valid_in      : in std_logic;
            tlast_in        : in std_logic;
            rd_en           : in std_logic;
            B_vector_out    : out std_logic_vector ((16*8)-1 downto 0);
            B_valid_out     : out std_logic;
            tlast_out       : out std_logic;
            empty           : out std_logic
        );
    end component;

  
    constant C_EL_SIZE  : integer := 16;
    constant C_BUS_EL   : integer := 8;
    constant CLK_PERIOD : time := 10 ns;

    signal clk          : std_logic := '0';
    signal resetn       : std_logic := '0';
    
    signal B_vector_in  : std_logic_vector((C_EL_SIZE*C_BUS_EL)-1 downto 0) := (others => '0');
    signal B_valid_in   : std_logic := '0';
    signal tlast_in     : std_logic := '0';
    signal rd_en        : std_logic := '0';
    
    signal B_vector_out : std_logic_vector((C_EL_SIZE*C_BUS_EL)-1 downto 0);
    signal B_valid_out  : std_logic;
    signal tlast_out    : std_logic;
    signal empty        : std_logic;

begin

    DUT: vector_fifo
        generic map (
            EL_SIZE => C_EL_SIZE,
            BUS_EL  => C_BUS_EL
        )
        port map (
            clk             => clk,
            resetn          => resetn,
            B_vector_in     => B_vector_in,
            B_valid_in      => B_valid_in,
            tlast_in        => tlast_in,
            rd_en           => rd_en,
            B_vector_out    => B_vector_out,
            B_valid_out     => B_valid_out,
            tlast_out       => tlast_out,
            empty           => empty
        );

    CLK_GEN : process
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
        
        -- B_vector_in <= x"0008" & x"0007" & x"0006" & x"0005" & 
        --                x"0004" & x"0003" & x"0002" & x"0001";
        -- tlast_in    <= '0';
        -- B_valid_in  <= '1';

        wait for CLK_PERIOD;
        
        B_vector_in <= x"1118" & x"1117" & x"1116" & x"1115" & 
                       x"1114" & x"1113" & x"1112" & x"1111";
        tlast_in    <= '1';
        B_valid_in  <= '1';

        wait for CLK_PERIOD;
        
        B_valid_in  <= '0';
        tlast_in    <= '0';
        B_vector_in <= (others => '0');
        
        wait for CLK_PERIOD * 5;
        
        rd_en <= '1';

        wait for CLK_PERIOD*2; 
        
        rd_en <= '0';
        
        wait;
    end process;

end architecture vector_fifo_arch_TB;