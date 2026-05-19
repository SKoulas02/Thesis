library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
-- 
-- Description:
-- This Module is used to calculate the output of a 2:4 GEMV calculation.
-- Input is elements for Matrix A, Vector B and the Indices needed for the calculations.
-- It is made up of FIFOs, an array of c_block_row components and an adder tree.
-- The module can be reused for different GEMV operations and is fully pipelined.
-- ----------------------------------------------------------------------------

entity two2four is
    generic(
        EL_SIZE     : integer := 16;    -- Bit size of each element
        BUS_EL      : integer := 4;    -- Maximum number of elements on bus

        A_IDX       : integer := 2;     -- Number of matrix elements
        B_IDX       : integer := 4;     -- Number of vector elements
        IND_NUM     : integer := 4;     -- Number of indices Bits

        A_ROWS      : integer := 8      -- Number of Rows of Matrix A
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        B_in        : in std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
        B_valid_in  : in std_logic;
        tlast_in    : in std_logic;

        A_in        : in std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
        A_valid     : in std_logic;
        
        indices     : in std_logic_vector (31 downto 0);
        ind_valid   : in std_logic;

        Cout        : out std_logic_vector (EL_SIZE-1 downto 0);
        Cvalid      : out std_logic;
        Ctlast      : out std_logic
    );
end entity two2four;


architecture two2four_arch of two2four is

    component c_block_row is
        generic(
            EL_SIZE     : integer := 16;    -- Bit size of each element
            A_IDX       : integer := 2;     -- Number of matrix elements
            B_IDX       : integer := 4;     -- Number of vector elements
            BUS_EL      : integer := 4;     -- Maximum number of elements on bus
            IND_NUM     : integer := 4      -- Number of Indices Bits
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;
            
            A_row       : in std_logic_vector ((BUS_EL*EL_SIZE/A_IDX)-1 downto 0);
            Indices     : in std_logic_vector ((BUS_EL/B_IDX)*IND_NUM-1 downto 0);
            B_vector_in : in std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);

            valid_in    : in std_logic;
            tlast_in    : in std_logic;
            
            B_vector_out: out std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
            valid_out   : out std_logic;
            tlast_out   : out std_logic;

            Cout        : out std_logic_vector ((BUS_EL*EL_SIZE/B_IDX)-1 downto 0);
            Cvalid      : out std_logic;
            Ctlast      : out std_logic
        );
    end component c_block_row;

    component matrix_fifo_64 is
        generic(
            EL_SIZE     : integer := 16;    -- Bits of each Element
            BUS_EL      : integer := 4;     -- Max elements on Bus
            A_IDX       : integer := 2;     -- Number of Matrix Elements (2to4)
            B_IDX       : integer := 4;     -- Number of Vector Elements (2to4)
            IND_NUM     : integer := 4;     -- Number of Indeces Bits
            A_ROWS      : integer := 8      -- Number of Rows of Matrix
            
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;

            A_in        : in std_logic_vector (BUS_EL*EL_SIZE-1 downto 0); 
            A_valid_in  : in std_logic;

            indices     : in std_logic_vector (31 downto 0);     -- Test for RR Logic
            ind_valid   : in std_logic;                          

            rd_en       : in std_logic_vector (A_ROWS-1 downto 0);

            A_out           : out std_logic_vector ((A_ROWS*BUS_EL*EL_SIZE/A_IDX)-1 downto 0);
            indices_out     : out std_logic_vector ((A_ROWS * IND_NUM * BUS_EL/B_IDX) - 1 downto 0);
            empty           : out std_logic_vector (A_ROWS-1 downto 0)
        );
    end component matrix_fifo_64;

    component vector_fifo_64 is
        generic(
            EL_SIZE     : integer := 16;    -- Bit size of each element
            BUS_EL      : integer := 4     -- Max elements on Bus
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
    end component vector_fifo_64;

    -- Internal Signals Vector FIFO

    signal rd_en_vector         : std_logic := '0';
    signal B_vector_fifo        : std_logic_vector ((EL_SIZE*BUS_EL)-1 downto 0) := (others => '0');
    signal B_valid_fifo         : std_logic := '0';
    signal tlast_fifo           : std_logic := '0';
    signal empty_vector_fifo    : std_logic := '0';

    -- Internal Signals Matrix FIFO

    signal rd_en_matrix         : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');
    signal A_matrix_fifo        : std_logic_vector ((A_ROWS*BUS_EL*EL_SIZE/A_IDX)-1 downto 0) := (others => '0');
    signal indices_fifo         : std_logic_vector ((A_ROWS * IND_NUM * BUS_EL/B_IDX) - 1 downto 0) := (others => '0');
    signal empty_matrix_fifo    : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');

    -- Internal Signals C Array

    type b_vectors_array is array (1 to A_ROWS) of std_logic_vector ((EL_SIZE*BUS_EL)-1 downto 0);
    signal b_vectors_internal : b_vectors_array := (others => (others => '0'));
    
    signal b_valid_internal : std_logic_vector (A_ROWS downto 0) := (others => '0');
    signal tlast_internal : std_logic_vector (A_ROWS downto 0) := (others => '0');

    type c_array is array (0 to A_ROWS-1) of std_logic_vector ((BUS_EL*EL_SIZE/B_IDX)-1 downto 0);
    signal c_internal : c_array := (others => (others => '0'));

    signal c_valid_internal : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');
    signal c_tlast_internal : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');

    signal counter_adder    : integer := 0;
    signal check_empty      : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');

    signal saved_en_matrix  : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');

    signal counter : integer := 0;

    signal proposed_rd_en : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');
    signal will_block : std_logic := '0';

    -- One-cycle-delayed rd_en used as per-row latch enable for the c_block_row
    -- chain. rd_en_matrix(i) is a 1-cycle pulse; with the Standard FIFO's
    -- 1-cycle read latency, rd_en_matrix_d(i) is high exactly in the cycle
    -- the popped beat is on A_matrix_fifo[row i]. Replaces the FIFO VALID
    -- flag (which is level, not pulse) as the latch trigger.
    signal rd_en_matrix_d : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');

begin

    proposed_rd_en <= (saved_en_matrix(A_ROWS-2 downto 0) & '1') when (empty_vector_fifo = '0' AND empty_matrix_fifo(0) = '0' AND rd_en_matrix(0) = '0') else (saved_en_matrix(A_ROWS-2 downto 0) & '0');

    will_block <= '1' when (proposed_rd_en AND empty_matrix_fifo) /= (proposed_rd_en'range => '0') else '0';
    
    MAIN_PROC : process(clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then

                

                rd_en_vector <= '0';
                rd_en_matrix <= (others => '0');
                rd_en_matrix_d <= (others => '0');

                saved_en_matrix <= (others => '0');
                counter <= 0;

                Cout <= (others => '0');
                Cvalid <= '0';
                Ctlast <= '0';

            else

                if c_tlast_internal(counter) = '1' then

                    Cout <= c_internal(counter);
                    Cvalid <= c_valid_internal(counter);

                    if counter = A_ROWS-1 then
                        Ctlast <= '1';
                        counter <= 0;
                    else
                        Ctlast <= '0';
                        counter <= counter + 1;
                    end if;

                else

                    Cout <= (others => '0');
                    Cvalid <= '0';
                    Ctlast <= '0';

                end if;

                if will_block = '1' then

                    rd_en_matrix <= (others => '0');
                    rd_en_vector <= '0';

                else

                    rd_en_matrix <= proposed_rd_en;
                    saved_en_matrix <= proposed_rd_en;
                    if empty_vector_fifo = '0' AND empty_matrix_fifo(0) = '0' AND rd_en_matrix(0) = '0' then
                        rd_en_vector <= '1';
                    else
                        rd_en_vector <= '0';
                    end if;

                end if;

                -- Pipeline rd_en_matrix by one cycle so it aligns with the
                -- cycle the popped beat is on A_matrix_fifo (FIFO Gen Standard
                -- mode + Embedded_Reg has 1-cycle read latency).
                rd_en_matrix_d <= rd_en_matrix;
                
            end if;
        end if;
    end process MAIN_PROC;


    VECTOR_FIFO_INSTANCE : vector_fifo_64
    generic map(
        EL_SIZE     => EL_SIZE,
        BUS_EL      => BUS_EL
    )
    port map(
        clk         => clk,
        resetn      => resetn,

        B_vector_in => B_in,
        B_valid_in  => B_valid_in,
        tlast_in    => tlast_in,

        rd_en       => rd_en_vector,        -- Control Signal

        B_vector_out    => B_vector_fifo,   -- *** INTERNAL SIGNAL INPUT OF C ROWS ***
        B_valid_out     => B_valid_fifo,

        tlast_out       => tlast_fifo,
        empty           => empty_vector_fifo
    );

    MATRIX_FIFO_INSTANCE : matrix_fifo_64
    generic map(
        EL_SIZE     => EL_SIZE,
        BUS_EL      => BUS_EL,
        A_IDX       => A_IDX,
        B_IDX       => B_IDX,
        IND_NUM     => IND_NUM,
        A_ROWS      => A_ROWS
    )
    port map(
        clk         => clk,
        resetn      => resetn,

        A_in        => A_in,
        A_valid_in  => A_valid,

        indices     => indices,
        ind_valid   => ind_valid,
        
        rd_en       => rd_en_matrix,        -- Control Signal
        
        A_out           => A_matrix_fifo,   -- *** INTERNAL SIGNAL INPUT OF C ROWS ***
        indices_out     => indices_fifo,    -- *** INTERNAL SIGNAL INPUT OF C ROWS ***
        empty           => empty_matrix_fifo
    );

    C_ARRAY_GEN : for i in 0 to A_ROWS-1 generate
        
        C_FIRST_ROW : if i = 0 generate
            C_FIRST_ROW_INSTANCE : c_block_row
            generic map(
                EL_SIZE     => EL_SIZE,
                A_IDX       => A_IDX,
                B_IDX       => B_IDX,
                BUS_EL      => BUS_EL,
                IND_NUM     => IND_NUM
            )
            port map(
                clk         => clk,
                resetn      => resetn,

                A_row       => A_matrix_fifo(((i+1)*BUS_EL*EL_SIZE/A_IDX)-1 downto i*BUS_EL*EL_SIZE/A_IDX),   -- From Matrix FIFO
                Indices     => indices_fifo(((i+1)*IND_NUM*BUS_EL/B_IDX)-1 downto i*IND_NUM*BUS_EL/B_IDX),    -- From Indices FIFO
                B_vector_in => B_vector_fifo,   -- From Vector FIFO

                valid_in    => rd_en_matrix_d(i),  -- 1-cycle pulse aligned with dout
                tlast_in    => tlast_fifo,
                
                B_vector_out    => b_vectors_internal(i+1),        
                valid_out       => b_valid_internal(i+1),       
                tlast_out       => tlast_internal(i+1),       

                Cout        => c_internal(i),
                Cvalid      => c_valid_internal(i),         
                Ctlast      => c_tlast_internal(i)          
            );
        end generate;
        
        C_REST_ARRAY : if i /= 0 generate
            C_ROWS_INSTANCE : c_block_row
            generic map(
                EL_SIZE     => EL_SIZE,
                A_IDX       => A_IDX,
                B_IDX       => B_IDX,
                BUS_EL      => BUS_EL,
                IND_NUM     => IND_NUM
            )
            port map(
                clk         => clk,
                resetn      => resetn,

                A_row       => A_matrix_fifo(((i+1)*BUS_EL*EL_SIZE/A_IDX)-1 downto i*BUS_EL*EL_SIZE/A_IDX),   -- From Matrix FIFO
                Indices     => indices_fifo(((i+1)*IND_NUM*BUS_EL/B_IDX)-1 downto i*IND_NUM*BUS_EL/B_IDX),    -- From Indices FIFO
                B_vector_in => b_vectors_internal(i),   -- From Internal B Vector of Previous Row

                valid_in    => rd_en_matrix_d(i),
                tlast_in    => tlast_internal(i),

                B_vector_out    => b_vectors_internal(i+1),        
                valid_out       => b_valid_internal(i+1),       
                tlast_out       => tlast_internal(i+1),       

                Cout        => c_internal(i),
                Cvalid      => c_valid_internal(i),         
                Ctlast      => c_tlast_internal(i)          
            );
        end generate;
    end generate;

end architecture two2four_arch;