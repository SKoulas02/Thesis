library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
-- 
-- Description:
-- This Module is used to store and retrieve matrix elements for GEMV operations.
-- Input is a bus of matrix elements for all rows and output is a bus of elements for each row of the matrix.
-- It is made up of FIFOs parallel to the number of matrix rows.
-- The module can be reused for different GEMV operations.
-- ----------------------------------------------------------------------------

entity matrix_fifo_512 is
    generic(
        EL_SIZE     : integer := 16;    -- Bits of each Element
        BUS_EL      : integer := 32;    -- Max elements on Bus
        A_IDX       : integer := 2;     -- Number of Matrix Elements (2to4)
        B_IDX       : integer := 4;     -- Number of Vector Elements (2to4)
        IND_NUM     : integer := 4;     -- Number of Indeces Bits
        A_ROWS      : integer := 64     -- Number of Rows of Matrix
        
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        A_in        : in std_logic_vector (BUS_EL*EL_SIZE-1 downto 0); 
        A_valid_in  : in std_logic;

        indices     : in std_logic_vector (BUS_EL*EL_SIZE-1 downto 0);
        ind_valid   : in std_logic;                          

        rd_en       : in std_logic_vector (A_ROWS-1 downto 0);

        A_out           : out std_logic_vector ((A_ROWS*BUS_EL*EL_SIZE/A_IDX)-1 downto 0);
        indices_out     : out std_logic_vector ((A_ROWS * IND_NUM * BUS_EL/B_IDX) - 1 downto 0);
        empty           : out std_logic_vector (A_ROWS-1 downto 0)
    );
end entity matrix_fifo_512;

architecture matrix_fifo_arch of matrix_fifo_512 is

    component fifo_gen_data is
        port(
            clk     : IN STD_LOGIC;
            srst    : IN STD_LOGIC;

            din     : IN STD_LOGIC_VECTOR((EL_SIZE*BUS_EL)-1 DOWNTO 0);
            wr_en   : IN STD_LOGIC;
            rd_en   : IN STD_LOGIC;
            dout    : OUT STD_LOGIC_VECTOR((EL_SIZE*BUS_EL/A_IDX)-1 DOWNTO 0);
            
            empty   : OUT STD_LOGIC
        );
    end component fifo_gen_data;

    component fifo_gen_indices is
        port(
            clk     : IN STD_LOGIC;
            srst    : IN STD_LOGIC;

            din     : IN STD_LOGIC_VECTOR((IND_NUM*BUS_EL/A_IDX)-1 DOWNTO 0);
            wr_en   : IN STD_LOGIC;
            rd_en   : IN STD_LOGIC;
            dout    : OUT STD_LOGIC_VECTOR((IND_NUM*BUS_EL/B_IDX)-1 DOWNTO 0);
            
            empty   : OUT STD_LOGIC
        );
    end component fifo_gen_indices;

    signal reset            : std_logic := '0';

    type matrix_array is array(natural range <>) of std_logic_vector ((BUS_EL*EL_SIZE/A_IDX)-1 downto 0);
    signal A_out_internal   : matrix_array(0 to A_ROWS-1);

    type indices_array is array(natural range <>) of std_logic_vector (IND_NUM*BUS_EL/B_IDX - 1 downto 0);
    signal indices_out_int  : indices_array (0 to A_ROWS-1);

    signal write_en_data     : std_logic_vector (A_rows-1 downto 0) := (others => '0');
    signal write_en_indices  : std_logic_vector (A_rows-1 downto 0) := (others => '0');
    
    signal write_reg_data    : std_logic_vector (A_rows-1 downto 0) := (others => '0'); 
    signal write_reg_indices : std_logic_vector (A_rows-1 downto 0) := (others => '0'); 

    signal empty_data        : std_logic_vector (A_ROWS-1 downto 0);
    signal empty_indices     : std_logic_vector (A_ROWS-1 downto 0);

    constant IND_PACK   : integer := 8;  -- Number of rows of indices FIFO, used for RR logic);

begin

    reset <= NOT resetn;
    
    write_en_data <= write_reg_data when (A_valid_in = '1' AND resetn = '1') else (others => '0');
    write_en_indices <= write_reg_indices when (ind_valid = '1' AND resetn = '1') else (others => '0');
    
    empty <= empty_data OR empty_indices;

    PACK_OUTPUT : for i in 0 to A_ROWS-1 generate
        A_out(((i+1) * (BUS_EL*EL_SIZE/A_IDX)) - 1 downto i * (BUS_EL*EL_SIZE/A_IDX)) <= A_out_internal(i);
        indices_out(((i+1) * IND_NUM * BUS_EL/B_IDX) - 1 downto i * IND_NUM * BUS_EL/B_IDX) <= indices_out_int(i);
    end generate;

    -- Logic to write to FIFOs in Round Robin fashion

    ROUND_ROBIN_DATA : process(clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then
                write_reg_data   <= ((0) => '1', others => '0');
                write_reg_indices <= (IND_PACK-1 downto 0 => '1', others => '0');
            else
                if A_valid_in = '1' then
                    write_reg_data   <= write_reg_data (A_ROWS-2 downto 0) & write_reg_data (A_ROWS-1);
                else
                    write_reg_data   <= write_reg_data;
                end if;
                if ind_valid = '1' then
                    write_reg_indices <= write_reg_indices (A_ROWS-IND_PACK-1 downto 0) & write_reg_indices (A_ROWS-1 downto A_ROWS-IND_PACK);
                else
                    write_reg_indices <= write_reg_indices; 
                end if;
            end if;
        end if;
    end process;

    -- Generate FIFOs for each row of the matrix and connect even and odd indexed rows to different halves of the input bus

    FIFO_DATA_GEN : for i in 0 to A_ROWS-1 generate

        FIFO_DATA : fifo_gen_data
        port map(
            clk     => clk,
            srst    => reset,

            din     => A_in((BUS_EL*EL_SIZE)-1 downto 0),
            wr_en   => write_en_data(i),
            rd_en   => rd_en(i),
            dout    => A_out_internal(i),

            empty   => empty_data(i)
        );
    end generate;

    FIFO_IND_GEN : for i in 0 to (A_ROWS/IND_PACK)-1 generate

        FIFO_PACK_INST : for j in 0 to IND_PACK-1 generate
            FIFO_IND : fifo_gen_indices
            port map(
                clk     => clk,
                srst    => reset,

                din     => indices(((IND_PACK-j)*IND_NUM*BUS_EL/A_IDX)-1 downto (IND_PACK-1-j)*IND_NUM*BUS_EL/A_IDX),
                wr_en   => write_en_indices(i*IND_PACK+j),
                rd_en   => rd_en(i*IND_PACK+j),
                dout    => indices_out_int(i*IND_PACK+j),

                empty   => empty_indices(i*IND_PACK+j)
            );
        end generate;
    end generate;


end architecture matrix_fifo_arch;