library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity top_module is
    generic(
        EL_SIZE     : integer := 16;    -- Bit size of each element
        BUS_EL      : integer := 8      -- Maximum number of elements on bus

        A_IDX       : integer := 2;     -- Number of matrix elements
        B_IDX       : integer := 4;     -- Number of vector elements
        IND_NUM     : integer := 3;     -- Number of Indeces Bits

        A_ROWS      : integer := 16;    -- Number of Rows of Matrix A
        B_COLS      : integer := 16     -- Number of Elements of Vector
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        B_in        : in std_logic_vector ((B_COLS*EL_SIZE)-1 downto 0);
        B_valid_in  : in std_logic;
        tlast_in    : in std_logic;

        A_in        : in std_logic_vector ((A_ROWS*BUS_EL*EL_SIZE/2)-1 downto 0);
        A_valid     : in std_logic_vector (A_ROWS-1 downto 0);
        indeces     : in std_logic_vector ((A_ROWS*BUS_EL*IND_NUM/2)-1 downto 0);

        Cout        : out std_logic_vector ((2*EL_SIZE*A_ROWS)-1 downto 0);
        Cvalid      : out std_logic_vector ((2*A_ROWS)-1 downto 0);
        Ctlast      : out std_logic_vector ((2*A_ROWS)-1 downto 0)
    );
end entity c_array;


architecture c_array_arch of c_array is

    component c_block_row is
        generic(
            EL_SIZE     : integer := 16;    -- Bit size of each element
            A_IDX       : integer := 2;     -- Number of matrix elements
            B_IDX       : integer := 4;     -- Number of vector elements
            BUS_EL      : integer := 8;     -- Maximum number of elements on bus
            IND_NUM     : integer := 3      -- Number of Indeces Bits
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;
            
            B_vector_in : in std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
            B_valid_in  : in std_logic;
            tlast_in    : in std_logic;

            A_row       : in std_logic_vector ((BUS_EL*EL_SIZE/2)-1 downto 0);
            A_valid     : in std_logic;
            indeces     : in std_logic_vector ((2*IND_NUM)-1 downto 0);
            
            B_vector_out: out std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
            B_valid_out : out std_logic;
            tlast_out   : out std_logic;

            Cout        : out std_logic_vector ((2*EL_SIZE)-1 downto 0);
            Cvalid      : out std_logic;
            Ctlast      : out std_logic
        );
    end component c_block_row;

    type array_B is array (0 to A_ROWS-1) of std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
    signal vector_B : array_B;

    
begin


end architecture c_array_arch;