library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use work.my_array_pkg.all;

entity reg_rarray is
    generic(
        DATAW   : integer := 16;
        BUSW    : integer := 512;
        N       : integer := 16;
        M       : integer := 16
    );
    port(
        clk     : std_logic;
        resetn  : std_logic;
        shift   : std_logic;
        DATA_IN : std_logic_vector (BUSW-1 downto 0);
        DATA_OUT: my_array (0 to (BUSW/DATAW)-1) (DATAW-1 downto 0)
    );
end entity reg_rarray;

architecture reg_rarray_arch of reg_rarray is




begin

    GEN_REGS : for i in 0 to (BUSW/DATAW)-1 generate

        process(clk)
        begin
            if rising_edge(clk) then
                if resetn = '0' then
                    DATA_OUT(i) <= (others => '0');
                else
                    if shift = '1' then
                        DATA_OUT(i) <= DATA_IN((i+1)*DATAW) - 1 downto i*DATAW
                    end if;
                end if;
            end if;
        end process;
        
    end generate GEN_REGS;


    process(clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then

            end if;
        end if;
    end process;

end architecture reg_rarray_arch;