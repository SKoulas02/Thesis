library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity matrix_shift_TB is
end entity matrix_shift_TB;

architecture shift_arch_TB of matrix_shift_TB is

    component matrix_shift is
        generic(
            DATAW   : integer := 16;
            BUSW    : integer := 512;
            N       : integer := 32
        );
        port(
            clk     : in std_logic;
            resetn  : in std_logic;
            shift   : in std_logic;
            DATA_IN : in std_logic_vector (BUSW-1 downto 0);
            DATA_OUT: out std_logic_vector ((N*DATAW)-1 downto 0)
        );
    end component;

    signal clk      : std_logic := '0';
    signal resetn   : std_logic := '0';
    signal shift    : std_logic := '0';
    signal DATA_IN  : std_logic_vector(511 downto 0) := (others => '0');
    signal DATA_OUT : std_logic_vector(1023 downto 0);

    constant CLK_PERIOD : time := 10 ns;

begin

    DUT: matrix_shift
        generic map (
            DATAW => 16,
            BUSW  => 512,
            N     => 64
        )
        port map (
            clk      => clk,
            resetn   => resetn,
            shift    => shift,
            DATA_IN  => DATA_IN,
            DATA_OUT => DATA_OUT
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
        
        resetn <= '0';
        shift  <= '0';
        wait for CLK_PERIOD * 5; 
        
        resetn <= '1';
        wait for CLK_PERIOD * 2;

        DATA_IN <= (others => '1'); 
        shift   <= '1';
        wait for CLK_PERIOD;

        DATA_IN <= (others => '0');
        shift   <= '1';
        wait for CLK_PERIOD;

        shift <= '0';
        wait for CLK_PERIOD * 10;

        for i in 0 to 40 loop
            
            if (i mod 2 = 0) then
                DATA_IN <= x"1111_2222_3333_4444_5555_6666_7777_8888_9999_AAAA_BBBB_CCCC_DDDD_EEEE_FFFF_0000_1111_2222_3333_4444_5555_6666_7777_8888_9999_AAAA_BBBB_CCCC_DDDD_EEEE_FFFF_0000";
            else
                DATA_IN <= x"AAAA_BBBB_CCCC_DDDD_EEEE_FFFF_0000_1111_2222_3333_4444_5555_6666_7777_8888_9999_AAAA_BBBB_CCCC_DDDD_EEEE_FFFF_0000_1111_2222_3333_4444_5555_6666_7777_8888_9999";
            end if;
            
            shift <= '1';
            wait for CLK_PERIOD;
        end loop;

        shift <= '0';
        wait for CLK_PERIOD * 10;
        
    end process;

end architecture shift_arch_TB;