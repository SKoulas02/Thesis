library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity indices_fifo is
    generic(
        IND_NUM     : integer := 3;     -- Number of Indeces Bits
        BUS_EL      : integer := 8;     -- Max elements on Bus
        A_IDX       : integer := 2;     -- Number of Matrix Elements (2to4)
        A_ROWS      : integer := 16     -- Number of Rows of Matrix
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        indices     : in std_logic_vector ((96)-1 downto 0);    -- All Rows fit in Bus
        ind_valid   : in std_logic;

        rd_en       : in std_logic_vector (A_ROWS-1 downto 0);

        indices_out     : out std_logic_vector ((A_ROWS * IND_NUM * 2) - 1 downto 0);
        ind_valid_out   : out std_logic_vector (A_ROWS-1 downto 0);
        empty           : out std_logic_vector (A_ROWS-1 downto 0)
    );
end entity indices_fifo;

architecture indices_fifo_arch of indices_fifo is

    component fifo_gen_ind is
        port(
            clk     : IN STD_LOGIC;
            srst    : IN STD_LOGIC;

            din     : IN STD_LOGIC_VECTOR(5 DOWNTO 0);
            wr_en   : IN STD_LOGIC;
            rd_en   : IN STD_LOGIC;
            dout    : OUT STD_LOGIC_VECTOR(5 DOWNTO 0);
            
            empty   : OUT STD_LOGIC;
            valid   : OUT STD_LOGIC
        );
    end component fifo_gen_ind;

    type indices_array is array(natural range <>) of std_logic_vector (5 downto 0);
    signal indices_out_int  : indices_array (0 to A_ROWS-1);
    
    signal reset    : std_logic;

begin

    reset   <= NOT resetn;

    PACK_OUTPUT : for i in 0 to A_ROWS-1 generate
        indices_out(((i+1) * IND_NUM * 2) - 1 downto i * IND_NUM * 2) <= indices_out_int(i);
    end generate;

    FIFO_GEN : for i in 0 to A_ROWS-1 generate

        FIFO_IND : fifo_gen_ind
        port map(
            clk     => clk,
            srst    => reset,

            din     => indices(((i+1)*IND_NUM*2)-1 downto i*IND_NUM*2),
            wr_en   => ind_valid,
            rd_en   => rd_en(i),
            dout    => indices_out_int(i),

            empty   => empty(i),
            valid   => ind_valid_out(i)
        );
    end generate;


end architecture indices_fifo_arch;