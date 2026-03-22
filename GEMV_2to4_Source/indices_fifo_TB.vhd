library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use work.indices_array_pkg.all;

entity indices_fifo_TB is
end entity indices_fifo_TB;

architecture indices_fifo_arch_TB of indices_fifo_TB is

    component indices_fifo is
        generic(
            IND_NUM     : integer := 3;
            BUS_EL      : integer := 8;
            A_IDX       : integer := 2;
            A_ROWS      : integer := 16
        );
        port(
            clk             : in std_logic;
            resetn          : in std_logic;
            indices         : in std_logic_vector ((97)-1 downto 0);
            ind_valid       : in std_logic;
            rd_en           : in std_logic_vector (A_ROWS-1 downto 0);
            indices_out     : out indices_array(0 to A_ROWS-1);
            ind_valid_out   : out std_logic_vector (A_ROWS-1 downto 0);
            empty           : out std_logic_vector (A_ROWS-1 downto 0)
        );
    end component;

    constant CLK_PERIOD : time := 10 ns;
    constant C_ROWS     : integer := 16;

    signal clk             : std_logic := '0';
    signal resetn          : std_logic := '0';
    signal indices         : std_logic_vector(96 downto 0) := (others => '0');
    signal ind_valid       : std_logic := '0';
    signal rd_en           : std_logic_vector(C_ROWS-1 downto 0) := (others => '0');
    signal indices_out     : indices_array(0 to C_ROWS-1);
    signal ind_valid_out   : std_logic_vector(C_ROWS-1 downto 0);
    signal empty           : std_logic_vector(C_ROWS-1 downto 0);

begin

    DUT: indices_fifo
    port map (
        clk             => clk,
        resetn          => resetn,
        indices         => indices,
        ind_valid       => ind_valid,
        rd_en           => rd_en,
        indices_out     => indices_out,
        ind_valid_out   => ind_valid_out,
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
        wait for CLK_PERIOD * 2;

        indices(5 downto 0)   <= "101010"; 
        indices(11 downto 6)  <= "110011";
        indices(96 downto 12) <= (others => '0');
        
        ind_valid <= '1'; 
        wait for CLK_PERIOD;
        
        ind_valid <= '0';
        indices   <= (others => '0');
        
        wait for CLK_PERIOD * 5;
        
        rd_en(0) <= '1';
        
        wait for CLK_PERIOD;

        rd_en(1) <= '1';

        wait for CLK_PERIOD;
        
        rd_en <= (others => '0');
        
        wait;
    end process;

end architecture indices_fifo_arch_TB;