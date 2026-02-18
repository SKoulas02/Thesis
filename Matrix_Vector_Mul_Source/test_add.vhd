library ieee;
use ieee.std_logic_1164.all;

entity adder_TB is
end adder_TB;

architecture adder_TB_arch of adder_TB is

    component adder_bf16 is
        port(

        );
    end component adder_bf16;

    -- Signals and such 

begin

    DUT : adder_bf16
        port map(
            A => asd,

        );

    STIMULUS : process
    begin

    end process STIMULUS;

end architecture adder_TB_arch;