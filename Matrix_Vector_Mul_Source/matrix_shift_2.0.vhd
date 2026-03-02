library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity matrix_shift is
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
end entity matrix_shift;

architecture matrix_shift_arch of matrix_shift is
    

    constant iterations     : natural := (N*DATAW/BUSW)-1;

    type matrix_t is array (0 to ROWS - 1, 0 to COLS - 1) of std_logic_vector(DATAW - 1 downto 0);
    signal matrix_reg : matrix_t;

    constant iterations     : natural := (COLS*DATAW/BUSW)-1;
    signal wr_rr    : integer range 0 to iterations := 0;
  
begin

    MAIN_PROCESS: process(clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then

                DATA_OUT <= (others => '0');
                wr_rr <= 0;

                for r in 0 to ROWS - 1 loop
                    for c in 0 to COLS - 1 loop

                        matrix_reg(r, c) <= (others => '0');

                    end loop;
                end loop;
            else
                if we = '1' then
                    
                    if wr_rr = iterations then
                        
                        for c in 0 to BUSW/DATAW - 1 loop
                            matrix_reg(c + (wr_rr * BUSW/DATAW), 0) <= DATA_IN(((c + 1) * DATAW) - 1 downto c * DATAW);
                        end loop;

                        for r in 0 to ROWS-1 loop
                            for c in 0 to COLS-2 loop
                                matrix_reg(r,c+1) <= matrix_reg(r,c);
                            end loop;
                        end loop;

                        wr_rr <= 0;
                        
                    else
                        
                        for c in 0 to BUSW/DATAW - 1 loop
                            matrix_reg(c + (wr_rr * BUSW/DATAW), 0) <= DATA_IN(((c + 1) * DATAW) - 1 downto c * DATAW);
                        end loop;

                        wr_rr <= wr_rr + 1;
                        
                    end if;
                else

                    if re = '1' then
                        for r in 0 to ROWS-1 loop
                        
                            for c in 0 to COLS - 2 loop
                                matrix_reg(r, c+1) <= matrix_reg(r,c);
                            end loop;
                        
                            DATA_OUT(((c + 1) * DATAW) - 1 downto c * DATAW) <= matrix_reg(r, COLS-1);

                        end loop;
                    end if;
                end if;
            end if;
        end if;
    end process MAIN_PROCESS;
    
end architecture matrix_shift_arch;
