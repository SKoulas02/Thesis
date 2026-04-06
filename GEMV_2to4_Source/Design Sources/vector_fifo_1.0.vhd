library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
-- 
-- Description:
-- This Module is used to store and retrieve vector elements for GEMV operations.
-- Input is a bus of vector elements and output is a bus of elements for GEMV operations.
-- It is made up of one FIFO.
-- The module can be reused for different GEMV operations.
-- ----------------------------------------------------------------------------

entity vector_fifo is
    generic(
        EL_SIZE     : integer := 16;    -- Bit size of each element
        BUS_EL      : integer := 8      -- Max elements on Bus
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        B_vector_in : in std_logic_vector((EL_SIZE*BUS_EL)-1 downto 0);
        B_valid_in  : in std_logic;
        tlast_in    : in std_logic;

        rd_en       : in std_logic;

        B_vector_out    : out std_logic_vector ((EL_SIZE*BUS_EL)-1 downto 0);
        B_valid_out     : out std_logic;
        tlast_out       : out std_logic;
        empty           : out std_logic
    );
end entity vector_fifo;

architecture vector_fifo_arch of vector_fifo is

    component fifo_gen_vector is
        port(
            clk     : IN STD_LOGIC;
            srst    : IN STD_LOGIC;

            din     : IN STD_LOGIC_VECTOR(128 DOWNTO 0);
            wr_en   : IN STD_LOGIC;
            rd_en   : IN STD_LOGIC;
            dout    : OUT STD_LOGIC_VECTOR(128 DOWNTO 0);
            
            empty   : OUT STD_LOGIC;
            valid   : OUT STD_LOGIC
        );
    end component fifo_gen_vector;

    signal reset    : std_logic;
    signal din      : std_logic_vector (128 downto 0);
    signal dout     : std_logic_vector (128 downto 0);

begin

    reset   <= NOT resetn;
    din     <= B_vector_in & tlast_in;
    
    B_vector_out    <= dout(128 downto 1);
    tlast_out       <= dout(0);
    
    FIFO_VECTOR : fifo_gen_vector
    port map(
        clk     => clk,
        srst    => reset,

        din     => din,
        wr_en   => B_valid_in,
        rd_en   => rd_en,
        dout    => dout,

        empty   => empty,
        valid   => B_valid_out
    );

end architecture vector_fifo_arch;