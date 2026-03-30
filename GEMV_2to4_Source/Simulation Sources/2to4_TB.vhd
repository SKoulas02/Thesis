library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity two2four_TB is
end entity two2four_TB;

architecture two2four_arch_TB of two2four_TB is

    component two2four is
        generic(
            EL_SIZE     : integer := 16;
            BUS_EL      : integer := 8;
            A_IDX       : integer := 2;
            B_IDX       : integer := 4;
            IND_NUM     : integer := 3;
            A_ROWS      : integer := 16;
            B_COLS      : integer := 16
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;

            B_in        : in std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
            B_valid_in  : in std_logic;
            tlast_in    : in std_logic;

            A_in        : in std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
            A_valid     : in std_logic;
            
            indices     : in std_logic_vector (96-1 downto 0);
            ind_valid   : in std_logic;

            Cout        : out std_logic_vector (EL_SIZE-1 downto 0);
            Cvalid      : out std_logic;
            Ctlast      : out std_logic
        );
    end component;

    constant clk_period : time := 10 ns;

    signal clk         : std_logic := '0';
    signal resetn      : std_logic := '0';
    
    signal B_in        : std_logic_vector(127 downto 0) := (others => '0');
    signal B_valid_in  : std_logic := '0';
    signal tlast_in    : std_logic := '0';
    
    signal A_in        : std_logic_vector(127 downto 0) := (others => '0');
    signal A_valid     : std_logic := '0';
    
    signal indices     : std_logic_vector(95 downto 0) := (others => '0');
    signal ind_valid   : std_logic := '0';

    signal Cout        : std_logic_vector(15 downto 0);
    signal Cvalid      : std_logic;
    signal Ctlast      : std_logic;

begin

    DUT: two2four
    port map (
        clk         => clk,
        resetn      => resetn,
        B_in        => B_in,
        B_valid_in  => B_valid_in,
        tlast_in    => tlast_in,
        A_in        => A_in,
        A_valid     => A_valid,
        indices     => indices,
        ind_valid   => ind_valid,
        Cout        => Cout,
        Cvalid      => Cvalid,
        Ctlast      => Ctlast
    );

    CLK_GEN : process
    begin
        clk <= '0';
        wait for clk_period / 2;
        clk <= '1';
        wait for clk_period / 2;
    end process;

    STIMULUS : process
    begin
        resetn <= '0';
        wait for clk_period * 5;

        resetn <= '1';
        wait for clk_period * 5;

        B_in        <= x"00000000000000000000000000004000";
        B_valid_in  <= '1';
        tlast_in    <= '1';

        A_in        <= x"00000000000000000000000000004000";
        A_valid     <= '1';

        indices     <= (others => '0');
        ind_valid   <= '1';

        -- wait for clk_period;

        -- tlast_in    <= '1';

        wait for clk_period;
        
        tlast_in    <= '0';
        B_valid_in  <= '0';
        A_valid     <= '0';
        ind_valid   <= '0';
        tlast_in    <= '0';
        
        wait;
    end process;

end architecture two2four_arch_TB;