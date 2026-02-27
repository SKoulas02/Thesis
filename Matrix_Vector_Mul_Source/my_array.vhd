library ieee;
use ieee.std_logic_1164.all;

package my_array_pkg is
    type my_array is array(natural range <>) of std_logic_vector (natural range <>);
end package my_array_pkg;