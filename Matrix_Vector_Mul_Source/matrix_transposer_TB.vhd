library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity matrix_transposer_TB is
end entity matrix_transposer_TB;

architecture transposer_TB of matrix_transposer_TB is

    component matrix_transposer is
        generic(
            DATAW       : integer := 16; 
            BUSW        : integer := 512;   
            COLS        : integer := 32; -- Elements of Input Full line
            ROWS        : integer := 32  -- Elements of Output Full line
        );
        port(
            clk         : in  std_logic;
            resetn      : in  std_logic;
            
            we          : in  std_logic; -- Write Enable
            DATA_IN     : in  std_logic_vector(BUSW - 1 downto 0);
            
            re          : in  std_logic; -- Read Enable
            DATA_OUT    : out std_logic_vector((COLS * DATAW) - 1 downto 0)
        );
    end component;

    signal clk      : std_logic := '0';
    signal resetn   : std_logic := '0';
    signal we       : std_logic := '0';
    signal re       : std_logic := '0';


    signal DATA_IN  : std_logic_vector(47 downto 0) := (others => '0');
    signal DATA_OUT : std_logic_vector(47 downto 0);

    constant CLK_PERIOD : time := 10 ns;

begin

    DUT: matrix_transposer
        generic map (
            DATAW   => 16,
            BUSW    => 48,
            COLS    => 3,
            ROWS    => 3
        )
        port map (
            clk     => clk,
            resetn  => resetn,
            we      => we,
            re      => re,
            DATA_IN => DATA_IN,
            DATA_OUT=> DATA_OUT
        );

    CLK_GEN: process
    begin
        clk <= '0';
        wait for CLK_PERIOD / 2;
        clk <= '1';
        wait for CLK_PERIOD / 2;
    end process;

    STIMULUS: process
    begin
        
        resetn  <= '0';
        we      <= '0';
        re      <= '0';

        wait for CLK_PERIOD * 5; 
        
        resetn <= '1';
        wait for CLK_PERIOD * 2;
        
        we      <= '1';
        DATA_IN <= x"1111_2222_3333";
        wait for CLK_PERIOD*1;

        DATA_IN <= x"AAAA_BBBB_CCCC";
        wait for CLK_PERIOD;
        
        DATA_IN <= x"4444_5555_6666";
        wait for CLK_PERIOD;

        -- DATA_IN <= x"1111_2222_3333_4444";
        -- wait for CLK_PERIOD*1;

        -- DATA_IN <= x"AAAA_BBBB_CCCC_DDDD";
        -- wait for CLK_PERIOD;

        we      <= '0';

        wait for CLK_PERIOD;

        re      <= '1';

        wait for CLK_PERIOD*4;

        re      <= '0';
        resetn  <= '0';

        wait;
    end process;

end architecture transposer_TB;