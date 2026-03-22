library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use work.matrix_array_pkg.all;

entity matrix_fifo is
    generic(
        EL_SIZE     : integer := 16;    -- Bits of each Element
        BUS_EL      : integer := 8;     -- Max elements on Bus
        A_IDX       : integer := 2;     -- Number of Matrix Elements (2to4)
        A_ROWS      : integer := 16     -- Number of Rows of Matrix
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        A_in        : in std_logic_vector (BUS_EL*EL_SIZE-1 downto 0); 
        A_valid_in  : in std_logic;

        rd_en       : in std_logic_vector (A_ROWS-1 downto 0);

        A_out           : out matrix_array(0 to A_ROWS-1);
        A_valid_out     : out std_logic_vector (A_ROWS-1 downto 0);
        empty           : out std_logic_vector (A_ROWS-1 downto 0)
    );
end entity matrix_fifo;

architecture matrix_fifo_arch of matrix_fifo is

    component fifo_gen_matrix is
        port(
            clk     : IN STD_LOGIC;
            srst    : IN STD_LOGIC;

            din     : IN STD_LOGIC_VECTOR(63 DOWNTO 0);
            wr_en   : IN STD_LOGIC;
            rd_en   : IN STD_LOGIC;
            dout    : OUT STD_LOGIC_VECTOR(63 DOWNTO 0);
            
            empty   : OUT STD_LOGIC;
            valid   : OUT STD_LOGIC
        );
    end component fifo_gen_matrix;

    signal reset        : std_logic;

    signal write_en     : std_logic_vector (A_rows-1 downto 0);

begin

    reset <= NOT resetn;
    
    ROUND_ROBIN : process(clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then
                write_en <= (1 downto 0 => '1', others => '0');
            else
                if A_valid_in = '1' then
                    write_en <= write_en (A_ROWS-3 downto 0) & write_en (A_ROWS-1 downto A_ROWS-2);
                end if;
            end if;
        end if;
    end process;

    FIFO_GEN : for i in 0 to A_ROWS-1 generate

        EVEN_INSTANCE : if i MOD 2 = 0 generate
            FIFO_EVEN : fifo_gen_matrix
            port map(
                clk     => clk,
                srst    => reset,

                din     => A_in((BUS_EL*EL_SIZE/2)-1 downto 0),
                wr_en   => write_en(i),
                rd_en   => rd_en(i),
                dout    => A_out(i),

                empty   => empty(i),
                valid   => A_valid_out(i)
            );
        end generate;

        ODD_INSTANCE : if i MOD 2 = 1 generate
            FIFO_ODD : fifo_gen_matrix
            port map(
                clk     => clk,
                srst    => reset,

                din     => A_in(BUS_EL*EL_SIZE-1 downto BUS_EL*EL_SIZE/2),
                wr_en   => write_en(i),
                rd_en   => rd_en(i),
                dout    => A_out(i),

                empty   => empty(i),
                valid   => A_valid_out(i)
            );
        end generate;
    end generate;


end architecture matrix_fifo_arch;