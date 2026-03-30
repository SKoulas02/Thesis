library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity adder_tree_TB is
end entity adder_tree_TB;

architecture adder_tree_arch_TB of adder_tree_TB is
    
    component adder_tree is
        generic(
            EL_SIZE     : integer := 16;
            EL_NUM      : integer := 2
        );
        port(
            clk             : in std_logic;
            resetn          : in std_logic;
            in_elements     : in std_logic_vector((EL_SIZE*EL_NUM)-1 downto 0);
            in_valid        : in std_logic;
            tlast           : in std_logic;
            out_elements    : out std_logic_vector(EL_SIZE-1 downto 0);
            out_valid       : out std_logic;
            tlast_out       : out std_logic
        );
    end component adder_tree;

    constant C_EL_SIZE      : integer := 16;
    constant C_EL_NUM       : integer := 4; -- Test with 4 elements (must be a power of 2 for the current DUT)
    constant C_CLK_PERIOD   : time    := 10 ns;

    signal clk          : std_logic := '0';
    signal resetn       : std_logic := '0';

    signal in_elements  : std_logic_vector((C_EL_SIZE*C_EL_NUM)-1 downto 0) := (others => '0');
    signal in_valid     : std_logic := '0';
    signal tlast        : std_logic := '0';

    signal out_elements : std_logic_vector(C_EL_SIZE-1 downto 0);
    signal out_valid    : std_logic;
    signal tlast_out    : std_logic;

begin

    DUT : adder_tree
    generic map (
        EL_SIZE => C_EL_SIZE,
        EL_NUM  => C_EL_NUM
    )
    port map (
        clk         => clk,
        resetn      => resetn,
        in_elements => in_elements,
        in_valid    => in_valid,
        tlast       => tlast,
        out_elements=> out_elements,
        out_valid   => out_valid,
        tlast_out   => tlast_out
    );

    CLK_GEN : process
    begin
        clk <= '0';
        wait for C_CLK_PERIOD/2;
        clk <= '1';
        wait for C_CLK_PERIOD/2;
    end process;

    STIMULUS : process
    begin
        resetn <= '0';
        wait for C_CLK_PERIOD * 5;
        resetn <= '1';
        wait for C_CLK_PERIOD*5;

        -- Prepare inputs (10 + 20 + 30 + 40 = 100) 100.0 = x"42C8"
        in_elements <=  x"4120" &   -- element 3
                        x"41A0" &   -- element 2
                        x"41F0" &   -- element 1
                        x"4220";    -- element 0
        in_valid    <= '1';
        tlast       <= '0';

        wait for C_CLK_PERIOD;

        tlast       <= '1';

        wait for C_CLK_PERIOD;

        in_valid <= '0';
        tlast    <= '0';
        in_elements <= (others => '0');

        wait;
    end process STIMULUS;

end architecture adder_tree_arch_TB;