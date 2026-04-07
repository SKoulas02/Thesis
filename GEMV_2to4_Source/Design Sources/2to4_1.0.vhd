library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

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
        BUS_EL      : integer := 8;     -- Maximum number of elements on bus

        A_IDX       : integer := 2;     -- Number of matrix elements
        B_IDX       : integer := 4;     -- Number of vector elements
        IND_NUM     : integer := 3;     -- Number of indices Bits

        A_ROWS      : integer := 16;    -- Number of Rows of Matrix A
        B_COLS      : integer := 16     -- Number of Elements of Vector
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        B_in        : in std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
        B_valid_in  : in std_logic;
        tlast_in    : in std_logic;

        A_in        : in std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
        A_valid     : in std_logic;
        
        indices     : in std_logic_vector (96-1 downto 0);          -- All Iteration indices fit in Bus in 1 Cycle
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
            BUS_EL      : integer := 8;     -- Maximum number of elements on bus
            IND_NUM     : integer := 3      -- Number of Indices Bits
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;
            
            B_vector_in : in std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
            B_valid_in  : in std_logic;
            tlast_in    : in std_logic;

            A_row       : in std_logic_vector ((BUS_EL*EL_SIZE/2)-1 downto 0);
            A_valid     : in std_logic;
            Indices     : in std_logic_vector ((BUS_EL*IND_NUM/B_IDX)-1 downto 0);
            
            B_vector_out: out std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
            B_valid_out : out std_logic;
            tlast_out   : out std_logic;

            Cout        : out std_logic_vector ((BUS_EL*EL_SIZE/B_IDX)-1 downto 0);
            Cvalid      : out std_logic;
            Ctlast      : out std_logic
        );
    end component c_block_row;

    component matrix_fifo is
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

            A_out           : out std_logic_vector ((A_ROWS*64)-1 downto 0);
            A_valid_out     : out std_logic_vector (A_ROWS-1 downto 0);
            empty           : out std_logic_vector (A_ROWS-1 downto 0)
        );
    end component matrix_fifo;

    component indices_fifo is
        generic(
            IND_NUM     : integer := 3;     -- Number of indices Bits
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
    end component indices_fifo;

    component vector_fifo is
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
    end component vector_fifo;

    component adder_tree is
        generic(
            EL_SIZE     : integer := 16;    -- Bit size of each element
            EL_NUM      : integer := 2      -- Input elements 
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;

            in_elements     : in std_logic_vector((EL_SIZE*EL_NUM)-1 downto 0);
            in_valid        : in std_logic;
            tlast           : in std_logic;

            out_elements    : out std_logic_vector(EL_SIZE-1 downto 0);
            out_valid       : out std_logic;
            tlast_out       : out std_logic
        );
    end component adder_tree;
    -- Inteernal Signals Vector FIFO

    signal rd_en_vector         : std_logic := '0';
    signal B_vector_fifo        : std_logic_vector ((EL_SIZE*BUS_EL)-1 downto 0) := (others => '0');
    signal B_valid_fifo         : std_logic := '0';
    signal tlast_fifo           : std_logic := '0';
    signal empty_vector_fifo    : std_logic := '0';

    -- Internal Signals Matrix FIFO

    signal rd_en_matrix         : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');
    signal A_matrix_fifo        : std_logic_vector ((A_ROWS*64)-1 downto 0) := (others => '0');
    signal A_valid_fifo         : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');
    signal empty_matrix_fifo    : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');

    -- Internal Signals Indices FIFO

    signal rd_en_indices        : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');
    signal indices_fifo_out     : std_logic_vector ((A_ROWS * IND_NUM * 2) - 1 downto 0) := (others => '0');
    signal ind_valid_fifo       : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');
    signal empty_indices_fifo   : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');

    -- Internal Signals C Array

    type b_vectors_array is array (1 to A_ROWS) of std_logic_vector ((EL_SIZE*BUS_EL)-1 downto 0);
    signal b_vectors_internal : b_vectors_array := (others => (others => '0'));
    
    signal b_valid_internal : std_logic_vector (A_ROWS downto 0) := (others => '0');
    signal tlast_internal : std_logic_vector (A_ROWS downto 0) := (others => '0');

    type c_array is array (0 to A_ROWS-1) of std_logic_vector ((BUS_EL*EL_SIZE/B_IDX)-1 downto 0);
    signal c_internal : c_array := (others => (others => '0'));

    signal c_valid_internal : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');
    signal c_tlast_internal : std_logic_vector (A_ROWS-1 downto 0) := (others => '0');


    type FSM_TYPE is (RESET, START);
    signal state : FSM_TYPE := RESET;

    type FSM_ADDER is (RESET, ADDING);
    signal adder_state : FSM_ADDER := RESET;

    signal adder_reg : std_logic_vector((EL_SIZE*(BUS_EL/B_IDX))-1 downto 0) := (others => '0');
    signal adder_reg_valid : std_logic := '0';
    signal adder_reg_tlast : std_logic := '0';

    signal counter_adder    : integer := 0;
begin


    MAIN_PROC : process(clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then
                state <= RESET;
                adder_state <= RESET;
                
                rd_en_vector <= '0';
                rd_en_matrix <= (others => '0');
                rd_en_indices <= (others => '0');
            else
                case state is
                when RESET =>
                    
                    if empty_vector_fifo = '0' AND empty_matrix_fifo(0) = '0' AND empty_indices_fifo(0) = '0' then
                        rd_en_vector <= '1';
                        rd_en_matrix <= rd_en_matrix(A_ROWS-2 downto 0) & '1';    
                        rd_en_indices <= rd_en_indices(A_ROWS-2 downto 0) & '1';
                        state <= START;
                    else
                        rd_en_matrix <= rd_en_matrix(A_ROWS-2 downto 0) & '0';    
                        rd_en_indices <= rd_en_indices(A_ROWS-2 downto 0) & '0';
                    end if;

                when START =>
                    rd_en_vector <= '0';
                    rd_en_matrix <= rd_en_matrix(A_ROWS-2 downto 0) & '0';    
                    rd_en_indices <= rd_en_indices(A_ROWS-2 downto 0) & '0';
                    state <= RESET;
                when others =>
                end case;

                case adder_state is
                when RESET =>

                    if c_tlast_internal(0) = '1' then
                        adder_reg <= c_internal(0);
                        adder_reg_valid <= '1';
                        adder_reg_tlast <= '0';
                        adder_state <= ADDING;
                        counter_adder <= 1;
                    else
                        adder_reg <= (others => '0');
                        adder_reg_valid <= '0';
                        adder_reg_tlast <= '0';
                    end if;
                when ADDING =>
                    if counter_adder = A_ROWS-1 then
                        adder_reg <= c_internal(A_ROWS-1);
                        adder_reg_tlast <= '1';
                        adder_state <= RESET;
                    else
                        counter_adder <= counter_adder + 1;
                        adder_reg <= c_internal(counter_adder);
                    end if;
                when others =>
                end case; 
            end if;
        end if;
    end process MAIN_PROC;


    VECTOR_FIFO_INSATNCE : vector_fifo
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

    MATRIX_FIFO_INSTACNE : matrix_fifo
    generic map(
        EL_SIZE     => EL_SIZE,
        BUS_EL      => BUS_EL,
        A_IDX       => A_IDX,
        A_ROWS      => A_ROWS
    )
    port map(
        clk         => clk,
        resetn      => resetn,

        A_in        => A_in,
        A_valid_in  => A_valid,
        
        rd_en       => rd_en_matrix,        -- Control Signal
        
        A_out           => A_matrix_fifo,   -- *** INTERNAL SIGNAL INPUT OF C ROWS ***
        A_valid_out     => A_valid_fifo,
        empty           => empty_matrix_fifo
    );

    INDICES_FIFO_INSTANCE : indices_fifo
    generic map(
        IND_NUM     => IND_NUM,
        BUS_EL      => BUS_EL,
        A_IDX       => A_IDX,
        A_ROWS      => A_ROWS
    )
    port map(
        clk         => clk,
        resetn      => resetn,

        indices     => indices,
        ind_valid   => ind_valid,
        
        rd_en       => rd_en_indices,       -- Control Signal
        
        indices_out     => indices_fifo_out,    -- *** INTERNAL SIGNAL INPUT OF C ROWS ***
        ind_valid_out   => ind_valid_fifo,
        empty           => empty_indices_fifo
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

                B_vector_in => B_vector_fifo,   -- From Vector FIFO
                B_valid_in  => B_valid_fifo,
                tlast_in    => tlast_fifo,

                A_row       => A_matrix_fifo(((i+1)*64)-1 downto i*64),   -- From Matrix FIFO
                A_valid     => A_valid_fifo(i),
                Indices     => indices_fifo_out(((i+1)*IND_NUM*2)-1 downto i*IND_NUM*2),    -- From Indices FIFO
                
                B_vector_out    => b_vectors_internal(i+1),        
                B_valid_out     => b_valid_internal(i+1),       
                tlast_out       => tlast_internal(i+1),       

                Cout        => c_internal(i),
                Cvalid      => c_valid_internal(i),         
                Ctlast      => c_tlast_internal(i)          
            );
        end generate;
        
        C_REST_ARRAY : if i > 0 generate
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

                B_vector_in => b_vectors_internal(i),   -- From Internal B Vector of Previous Row
                B_valid_in  => b_valid_internal(i),
                tlast_in    => tlast_internal(i),

                A_row       => A_matrix_fifo(((i+1)*64)-1 downto i*64),   -- From Matrix FIFO
                A_valid     => A_valid_fifo(i),
                Indices     => indices_fifo_out(((i+1)*IND_NUM*2)-1 downto i*IND_NUM*2),    -- From Indices FIFO
                
                B_vector_out    => b_vectors_internal(i+1),        
                B_valid_out     => b_valid_internal(i+1),       
                tlast_out       => tlast_internal(i+1),       

                Cout        => c_internal(i),
                Cvalid      => c_valid_internal(i),         
                Ctlast      => c_tlast_internal(i)          
            );
        end generate;
    end generate;

    ADDER_TREE_INSTANCE : adder_tree
    generic map(
        EL_SIZE => EL_SIZE,
        EL_NUM  => BUS_EL/B_IDX
    )
    port map(
        clk         => clk,
        resetn      => resetn,

        in_elements     => adder_reg,
        in_valid        => adder_reg_valid,
        tlast           => adder_reg_tlast,

        out_elements    => Cout,
        out_valid       => Cvalid,
        tlast_out       => Ctlast
    );

end architecture two2four_arch;