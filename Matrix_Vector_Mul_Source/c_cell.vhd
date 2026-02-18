library ieee;
use ieee.std_logic_1164.all;

entity c_cell is
    port (
        Clk     : in std_logic;
        Valid   : in std_logic;
        Reset   : in std_logic;

        Ain     : in std_logic_vector (15 downto 0);
        Bin     : in std_logic_vector (15 downto 0);

        Bout    : out std_logic_vector (15 downto 0);
        Ci      : out std_logic_vector (31 downto 0)
    );
end entity c_cell;

architecture c_cell_arch of c_cell is

    component fp_mult_bf16
        port(
            aclk                : in std_logic;
            s_axis_a_tvalid     : in std_logic;
            s_axis_a_tdata      : in std_logic_vector(15 downto 0);
            s_axis_b_tvalid     : in std_logic;
            s_axis_b_tdata      : in std_logic_vector(15 downto 0);
            m_axis_result_tvalid: out std_logic;
            m_axis_result_tdata : out std_logic_vector(15 downto 0)
        );
    end component fp_mult_bf16;

    component fp_add_fp32
        port(
            
        );
    end component fp_add_fp32;

    signal Mulres   : std_logic_vector (31 downto 0);
    signal Addres   : std_logic_vector (31 downto 0);
    signal Bint     : std_logic_vector (15 downto 0);
    signal Cint     : std_logic_vector (31 downto 0);

begin




    Bint <= Bin;
    Bout <= Bint;
end architecture c_cell_arch;