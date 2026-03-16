library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity bus_rows_TB is
end entity bus_rows_TB;

architecture bus_rows_arch_TB of bus_rows_TB is

    component bus_rows is
        generic(
            EL_WIDTH    : integer := 16
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;
            valid_in    : in std_logic;
            data_in     : in std_logic_vector (EL_WIDTH-1 downto 0);
            rd_en_i     : in std_logic;
            tlast       : in std_logic;
            tlast_out   : out std_logic;
            valid_out   : out std_logic;
            empty       : out std_logic;
            data_out    : out std_logic_vector (EL_WIDTH-1 downto 0)
        );
    end component;

    constant C_EL_WIDTH : integer := 16;
    constant CLK_PERIOD : time := 10 ns;

    signal clk         : std_logic := '0';
    signal resetn      : std_logic := '0';
    signal valid_in    : std_logic := '0';
    signal data_in     : std_logic_vector(C_EL_WIDTH-1 downto 0) := (others => '0');
    signal rd_en_i     : std_logic := '0';
    signal tlast       : std_logic := '0';
    
    signal tlast_out   : std_logic;
    signal valid_out   : std_logic;
    signal empty       : std_logic;
    signal data_out    : std_logic_vector(C_EL_WIDTH-1 downto 0);

begin

    DUT: bus_rows
    generic map (
        EL_WIDTH => C_EL_WIDTH
    )
    port map (
        clk         => clk,
        resetn      => resetn,
        valid_in    => valid_in,
        data_in     => data_in,
        rd_en_i     => rd_en_i,
        tlast       => tlast,
        tlast_out   => tlast_out,
        valid_out   => valid_out,
        empty       => empty,
        data_out    => data_out
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
        valid_in <= '0';
        rd_en_i <= '0';
        tlast <= '0';
        data_in <= (others => '0');
        wait for CLK_PERIOD * 5;
        
        resetn <= '1';
        wait for CLK_PERIOD * 5;

        valid_in <= '1';
        data_in  <= x"3F80"; 
        tlast    <= '0';
        wait for CLK_PERIOD;
        
        valid_in <= '1';
        data_in  <= x"4000"; 
        tlast    <= '1';       
        wait for CLK_PERIOD;
        
        valid_in <= '0';
        tlast    <= '0';
        data_in  <= (others => '0');
        wait for CLK_PERIOD * 5; 
        
        rd_en_i <= '1';
        wait for CLK_PERIOD * 3; 
        
        rd_en_i <= '0';
        
        wait for CLK_PERIOD;

        valid_in <= '1';
        data_in  <= x"3F80"; 
        tlast    <= '0';
        wait for CLK_PERIOD*10;
        
        valid_in <= '1';
        data_in  <= x"4000"; 
        tlast    <= '1';       
        wait for CLK_PERIOD;
        
        valid_in <= '0';
        tlast    <= '0';
        data_in  <= (others => '0');
        wait for CLK_PERIOD * 5; 

        rd_en_i <= '1';
        wait for CLK_PERIOD * 3; 

        rd_en_i <= '0';

        wait;
    end process;

end architecture bus_rows_arch_TB;