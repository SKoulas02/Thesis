library IEEE;
use IEEE.std_logic_1164.all;

package indices_array_pkg is
    type indices_array is array(natural range <>) of std_logic_vector (5 downto 0);
end package indices_array_pkg;
