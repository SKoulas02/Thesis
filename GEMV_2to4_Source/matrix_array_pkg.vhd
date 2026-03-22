library IEEE;
use IEEE.std_logic_1164.all;

package matrix_array_pkg is
    type matrix_array is array(natural range <>) of std_logic_vector (63 downto 0);
end package matrix_array_pkg;
