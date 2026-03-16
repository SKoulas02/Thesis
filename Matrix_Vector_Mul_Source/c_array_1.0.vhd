library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity c_array is
    generic(
        ROWS    : integer := 1;
        COLS    : integer := 16;
        EL_WIDTH: integer := 16
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;
        
        row_data    : in std_logic_vector ((ROWS*EL_WIDTH)-1 downto 0);
        column_data : in std_logic_vector ((COLS*EL_WIDTH)-1 downto 0);
        row_valid   : in std_logic_vector (ROWS-1 downto 0);
        column_valid: in std_logic_vector (COLS-1 downto 0);
        tlast       : in std_logic;

        C_out       : out std_logic_vector ((COLS*ROWS*EL_WIDTH)-1 downto 0);
        C_valid_out : out std_logic_vector (COLS-1 downto 0);
        C_tlast_out : out std_logic
    );
end entity c_array;

architecture c_array_arch of c_array is

    component c_cell is
    generic(
        EL_WIDTH    : integer := 16
    );
    port(
        clk     : in std_logic;
        resetn  : in std_logic;
        
        Ain     : in std_logic_vector (EL_WIDTH-1 downto 0);
        avalid  : in std_logic;

        Bin     : in std_logic_vector (EL_WIDTH-1 downto 0);
        bvalid  : in std_logic;
        tlast_in: in std_logic;
        
        Aout        : out std_logic_vector (EL_WIDTH-1 downto 0);
        avalidout   : out std_logic;

        Bout        : out std_logic_vector (EL_WIDTH-1 downto 0);
        bvalidout   : out std_logic;
        tlast_out   : out std_logic;
        
        Ci      : out std_logic_vector (EL_WIDTH-1 downto 0);
        Cvalid  : out std_logic;
        Ctlast  : out std_logic
    );
end component c_cell;



    type matrix_B is array (1 to COLS) of std_logic_vector (EL_WIDTH-1 downto 0);
    signal B_int    : matrix_B;
    
    signal B_valid  : std_logic_vector (COLS downto 1) := (others => '0');
    signal tlast_int: std_logic_vector (COLS downto 1) := (others => '0');
    signal Ctlast_int   : std_logic_vector (COLS-1 downto 0) := (others => '0');
begin

    C_tlast_out <= Ctlast_int(COLS-1);

    C_ARRAY : for i in 0 to COLS-1 generate

        C_FIRST : if i = 0 generate
            C_CELL_FIRST : c_cell
            port map(
                clk         => clk,
                resetn      => resetn,

                Ain         => column_data(((i+1)*EL_WIDTH)-1 downto i*EL_WIDTH),
                avalid      => column_valid(i),
                

                Bin         => row_data(EL_WIDTH-1 downto 0),
                bvalid      => row_valid(0),
                tlast_in    => tlast,

                Bout        => B_int(1),
                bvalidout   => B_valid(1),
                tlast_out   => tlast_int(1),

                Ci          => C_out(((i+1)*EL_WIDTH)-1 downto i*EL_WIDTH),
                Cvalid      => C_valid_out(i),
                Ctlast      => Ctlast_int(0)
            );
        end generate;

        C_ELSE : if i > 0 generate
            C_CELL_INSTANCE : c_cell
            port map(
                clk         => clk,
                resetn      => resetn,

                Ain         => column_data(((i+1)*EL_WIDTH)-1 downto i*EL_WIDTH),
                avalid      => column_valid(i),

                Bin         => B_int(i),
                bvalid      => B_valid(i),
                tlast_in    => tlast_int(i),

                Bout        => B_int(i+1),
                bvalidout   => B_valid(i+1),
                tlast_out   => tlast_int(i+1),

                Ci          => C_out(((i+1)*EL_WIDTH)-1 downto i*EL_WIDTH),
                Cvalid      => C_valid_out(i),
                Ctlast      => Ctlast_int(i)
            );
        end generate;
    end generate;
end architecture c_array_arch;