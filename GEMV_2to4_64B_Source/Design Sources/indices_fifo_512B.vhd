library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
-- 
-- Description:
-- This Module is used to store and retrieve indices for GEMV operations.
-- Input is a bus of indices for all rows of the matrix and output is a bus of indices for each row of the matrix.
-- It is made up of FIFOs parallel to the number of matrix rows.
-- The module can be reused for different GEMV operations.
-- ----------------------------------------------------------------------------

entity indices_fifo_1024 is
    generic(
        IND_NUM     : integer := 3;     -- Number of Indeces Bits
        BUS_EL      : integer := 32;    -- Max elements on Bus
        EL_SIZE     : integer := 16;    -- Bit size of each element
        A_IDX       : integer := 2;     -- Number of Matrix Elements (2to4)
        B_IDX       : integer := 4;     -- Number of Vector Elements (2to4)
        A_ROWS      : integer := 128    -- Number of Rows of Matrix
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        indices     : in std_logic_vector ( (2 ** integer(floor(log2(real((B_IDX*EL_SIZE)/IND_NUM))))) * IND_NUM*BUS_EL/B_IDX -1 downto 0);
        ind_valid   : in std_logic;

        rd_en       : in std_logic_vector (A_ROWS-1 downto 0);

        indices_out     : out std_logic_vector ((A_ROWS * IND_NUM * BUS_EL/B_IDX) - 1 downto 0);
        ind_valid_out   : out std_logic_vector (A_ROWS-1 downto 0);
        empty           : out std_logic_vector (A_ROWS-1 downto 0)
    );
end entity indices_fifo_1024;

architecture indices_fifo_arch of indices_fifo_1024 is

    component fifo_gen_ind is
        port(
            clk     : IN STD_LOGIC;
            srst    : IN STD_LOGIC;

            din     : IN STD_LOGIC_VECTOR(23 DOWNTO 0);
            wr_en   : IN STD_LOGIC;
            rd_en   : IN STD_LOGIC;
            dout    : OUT STD_LOGIC_VECTOR(23 DOWNTO 0);
            
            empty   : OUT STD_LOGIC;
            valid   : OUT STD_LOGIC
        );
    end component fifo_gen_ind;

    type indices_array is array(natural range <>) of std_logic_vector (IND_NUM*BUS_EL/B_IDX - 1 downto 0);
    signal indices_out_int  : indices_array (0 to A_ROWS-1);
    
    signal write_en     : std_logic_vector (A_rows-1 downto 0) := (others => '0');
    signal write_reg    : std_logic_vector (A_rows-1 downto 0) := (others => '0'); 

    signal reset        : std_logic := '0';

    constant IND_ROWS   : integer := (2 ** integer(floor(log2(real((B_IDX*EL_SIZE)/IND_NUM)))));

begin

    reset <= NOT resetn;
    write_en <= write_reg when (ind_valid = '1' AND resetn = '1') else (others => '0');


    PACK_OUTPUT : for i in 0 to A_ROWS-1 generate
        indices_out(((i+1) * IND_NUM * BUS_EL/B_IDX) - 1 downto i * IND_NUM * BUS_EL/B_IDX) <= indices_out_int(i);
    end generate;

    ROUND_ROBIN : process(clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then
                write_reg   <= (IND_ROWS-1 downto 0 => '1', others => '0');
            else
                if ind_valid = '1' then
                    write_reg   <= write_reg (A_ROWS-IND_ROWS-1 downto 0) & write_reg (A_ROWS-1 downto A_ROWS-IND_ROWS);
                end if;
            end if;
        end if;
    end process;

    -- Generate FIFOs for each row of the matrix
    FIFO_GEN : for i in 0 to (A_ROWS/IND_ROWS)-1 generate

        FIFO_REP : for j in 0 to IND_ROWS-1 generate
            
            FIFO_IND : fifo_gen_ind
            port map(
                clk     => clk,
                srst    => reset,

                din     => indices(((j+1)*IND_NUM*BUS_EL/B_IDX)-1 downto j*IND_NUM*BUS_EL/B_IDX),
                wr_en   => write_en(i*IND_ROWS+j),
                rd_en   => rd_en(i*IND_ROWS+j),
                dout    => indices_out_int(i*IND_ROWS+j),

                empty   => empty(i*IND_ROWS+j),
                valid   => ind_valid_out(i*IND_ROWS+j)
            );
        end generate;
    end generate;


end architecture indices_fifo_arch;