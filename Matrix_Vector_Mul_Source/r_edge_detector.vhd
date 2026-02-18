library ieee;
use ieee.std_logic_1164.all;

entity r_edge_detector is
    port(
        input   : in std_logic;
        clk     : in std_logic;
        resetn  : in std_logic;

        output  : out std_logic
    );
end entity;

architecture redge_arch of r_edge_detector is

    signal in_del   : std_logic :='0';

begin

    process(clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then
                in_del <= '0';
            else
                in_del <= input;
            end if;
        end if;
    end process;

    output <= input AND (NOT in_del);
end architecture;
