library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;


-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
-- 
-- Description:
-- This Module is used to calculate the output of a single 2:4 GEMV operation.
-- Input is 2 elements of Matrix A and 4 elements of Vector B.
-- It is made up of 2 multipliers, 1 adder and 1 accumulator that are fully pipelined.
-- The module can be reused for different GEMV operations and has a through-put of 1 cycle.
-- ----------------------------------------------------------------------------

entity c_block is
    generic(
        EL_SIZE : integer := 16;    -- Bit size of each element
        A_IDX   : integer := 2;     -- Number of matrix elements
        B_IDX   : integer := 4;     -- Number of vector elements
        IND_NUM : integer := 3      -- Number of Indices Bits
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        A_in        : in std_logic_vector ((EL_SIZE*A_IDX)-1 downto 0);     -- Matrix Input
        A_valid     : in std_logic;
        Indices     : in std_logic_vector (IND_NUM-1 downto 0);

        B_in        : in std_logic_vector ((EL_SIZE*B_IDX)-1 downto 0);     -- Vector Input
        B_valid     : in std_logic;
        
        tlast_in    : in std_logic;

        Bout        : out std_logic_vector ((EL_SIZE*B_IDX)-1 downto 0);     -- Vector Output
        tlast_out   : out std_logic;
        B_valid_out : out std_logic;

        Cout        : out std_logic_vector (EL_SIZE-1 downto 0);        -- Calculated Output Element
        Cvalid      : out std_logic;
        Ctlast      : out std_logic
    );
end entity c_block;



architecture c_block_arch of c_block is

    component multiplier_wrapper is
    port(
        aclk                    : in std_logic;
        aresetn                 : in std_logic;
        s_axis_a_tvalid         : in std_logic;
        s_axis_a_tdata          : in std_logic_vector (EL_SIZE-1 downto 0);
        s_axis_b_tvalid         : in std_logic;
        s_axis_b_tdata          : in std_logic_vector (EL_SIZE-1 downto 0);
        s_axis_b_tlast          : in std_logic;
        m_axis_result_tvalid    : out std_logic;
        m_axis_result_tdata     : out std_logic_vector (EL_SIZE-1 downto 0);
        m_axis_result_tlast     : out std_logic
    );
    end component multiplier_wrapper;

    component adder_wrapper is
    port(
        aclk                    : in std_logic;
        aresetn                 : in std_logic;
        s_axis_a_tvalid         : in std_logic;
        s_axis_a_tdata          : in std_logic_vector (EL_SIZE-1 downto 0);
        s_axis_b_tvalid         : in std_logic;
        s_axis_b_tdata          : in std_logic_vector (EL_SIZE-1 downto 0);
        s_axis_b_tlast          : in std_logic;
        m_axis_result_tvalid    : out std_logic;
        m_axis_result_tdata     : out std_logic_vector (EL_SIZE-1 downto 0);
        m_axis_result_tlast     : out std_logic
    );
    end component adder_wrapper;

    component accumulator_wrapper is
    port(
        aclk                    : in std_logic;
        aresetn                 : in std_logic;
        s_axis_a_tvalid         : in std_logic;
        s_axis_a_tdata          : in std_logic_vector (EL_SIZE-1 downto 0);
        s_axis_a_tlast          : in std_logic;
        m_axis_result_tvalid    : out std_logic;
        m_axis_result_tdata     : out std_logic_vector (EL_SIZE-1 downto 0);
        m_axis_result_tlast     : out std_logic
    );
    end component accumulator_wrapper;

    signal A_internal : std_logic_vector ((EL_SIZE*A_IDX)-1 downto 0) := (others => '0');
    signal B_internal : std_logic_vector ((EL_SIZE*A_IDX)-1 downto 0) := (others => '0');
    signal B_reg      : std_logic_vector ((EL_SIZE*B_IDX)-1 downto 0) := (others => '0');

    signal A_valid_internal : std_logic := '0';
    signal B_valid_internal : std_logic := '0';

    signal tlast_internal   : std_logic := '0';

    signal multi_out_1_data : std_logic_vector (EL_SIZE-1 downto 0) := (others => '0');
    signal multi_out_1_valid: std_logic := '0';
    signal multi_out_1_tlast: std_logic := '0';

    signal multi_out_2_data : std_logic_vector (EL_SIZE-1 downto 0) := (others => '0');
    signal multi_out_2_valid: std_logic := '0';

    signal adder_out_valid  : std_logic := '0';
    signal adder_out_data   : std_logic_vector (EL_SIZE-1 downto 0) := (others => '0');
    signal adder_out_tlast  : std_logic := '0';

begin

    MAIN_PROC : process (clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then                    -- Check for reset and initialize all signals
                A_internal <= (others => '0');
                B_internal <= (others => '0');

                A_valid_internal <= '0';
                B_valid_internal <= '0';

                tlast_internal   <= '0';

                Bout        <= (others => '0');
                tlast_out   <= '0';
                B_valid_out <= '0';
                -- B_reg       <= (others => '0');
                
            else

                case Indices is                     -- Select the appropriate 2 elements of B based on the Indices input
                    when "000" => 

                        B_internal <= B_in ((EL_SIZE*2)-1 downto EL_SIZE) & B_in (EL_SIZE-1 downto 0);

                    when "001" => 

                        B_internal <= B_in ((EL_SIZE*3)-1 downto (EL_SIZE*2)) & B_in (EL_SIZE-1 downto 0);

                    when "010" =>

                        B_internal <= B_in ((EL_SIZE*4)-1 downto (EL_SIZE*3)) & B_in (EL_SIZE-1 downto 0);

                    when "011" =>

                        B_internal <= B_in ((EL_SIZE*3)-1 downto (EL_SIZE*2)) & B_in ((EL_SIZE*2)-1 downto EL_SIZE);

                    when "100" =>

                        B_internal <= B_in ((EL_SIZE*4)-1 downto (EL_SIZE*3)) & B_in ((EL_SIZE*2)-1 downto EL_SIZE);

                    when "101" =>

                        B_internal <= B_in ((EL_SIZE*4)-1 downto (EL_SIZE*3)) & B_in ((EL_SIZE*3)-1 downto EL_SIZE*2);

                    when others =>
                        B_internal <= (others => '0');
                end case;
                
                -- Set signals according to the input values and pass them to the multipliers and pass-through registers.
                A_internal <= A_in;

                A_valid_internal <= A_valid;
                B_valid_internal <= B_valid;

                tlast_internal   <= tlast_in;

                -- B_reg       <= B_in;
                Bout        <= B_in;
                tlast_out   <= tlast_in;
                B_valid_out <= B_valid;

            end if;
        end if;
    end process MAIN_PROC;

    -- 2 Multipliers => Adder => Accumulator Dataflow
    MULTI_INSTANCE_1 : multiplier_wrapper
    port map(
        aclk                    => clk,            
        aresetn                 => resetn,
        s_axis_a_tvalid         => A_valid_internal,
        s_axis_a_tdata          => A_internal (EL_SIZE-1 downto 0),
        s_axis_b_tvalid         => B_valid_internal,
        s_axis_b_tdata          => B_internal (EL_SIZE-1 downto 0),
        s_axis_b_tlast          => tlast_internal,
        m_axis_result_tvalid    => multi_out_1_valid,
        m_axis_result_tdata     => multi_out_1_data,
        m_axis_result_tlast     => multi_out_1_tlast
    );

    MULTI_INSTANCE_2 : multiplier_wrapper
    port map(
        aclk                    => clk,            
        aresetn                 => resetn,
        s_axis_a_tvalid         => A_valid_internal,
        s_axis_a_tdata          => A_internal ((EL_SIZE*2)-1 downto EL_SIZE),
        s_axis_b_tvalid         => B_valid_internal,
        s_axis_b_tdata          => B_internal ((EL_SIZE*2)-1 downto EL_SIZE),
        s_axis_b_tlast          => tlast_internal,
        m_axis_result_tvalid    => multi_out_2_valid,
        m_axis_result_tdata     => multi_out_2_data,
        m_axis_result_tlast     => open
    );

    ADDER_INSTANCE : adder_wrapper
    port map(
        aclk                    => clk,
        aresetn                 => resetn,
        s_axis_a_tvalid         => multi_out_1_valid,
        s_axis_a_tdata          => multi_out_1_data,
        s_axis_b_tvalid         => multi_out_2_valid,
        s_axis_b_tdata          => multi_out_2_data,
        s_axis_b_tlast          => multi_out_1_tlast,
        m_axis_result_tvalid    => adder_out_valid,
        m_axis_result_tdata     => adder_out_data,
        m_axis_result_tlast     => adder_out_tlast
    );

    ACCUM_INSTANCE : accumulator_wrapper
    port map(
        aclk                    => clk,
        aresetn                 => resetn,
        s_axis_a_tvalid         => adder_out_valid,
        s_axis_a_tdata          => adder_out_data,
        s_axis_a_tlast          => adder_out_tlast,
        m_axis_result_tvalid    => Cvalid,
        m_axis_result_tdata     => Cout,
        m_axis_result_tlast     => Ctlast
    );
end architecture c_block_arch;