library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;


-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
-- 
-- Description:
-- This Module is used to calculate the output of a single 2:4 GEMV calculation.
-- Input is 2 elements of Matrix W and 4 elements of Vector A.
-- It is made up of 2 multipliers and 1 adder that are fully pipelined.
-- The module can be reused for different GEMV operations and has a through-put of 1 cycle.
-- ----------------------------------------------------------------------------

entity c_block is
    generic(
        EL_SIZE : integer := 16;    -- Bit size of each element
        W_IDX   : integer := 2;     -- Number of matrix elements
        A_IDX   : integer := 32;    -- Number of vector elements
        IND_NUM : integer := 10     -- Number of Indices Bits
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        W_in        : in std_logic_vector ((EL_SIZE*W_IDX)-1 downto 0);     -- Matrix Input
        Indices     : in std_logic_vector (IND_NUM-1 downto 0);             -- Indices Input
        A_in        : in std_logic_vector ((EL_SIZE*A_IDX)-1 downto 0);     -- Vector Input
        
        valid       : in std_logic;                                     
        tlast_in    : in std_logic;

        Cout        : out std_logic_vector (EL_SIZE-1 downto 0);            -- Calculated Output Element
        Cvalid      : out std_logic;
        Ctlast      : out std_logic
    );
end entity c_block;



architecture c_block_arch of c_block is

    component multiplier_wrapper is
    generic(
        EL_SIZE : integer := 16
    );
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
    generic(
        EL_SIZE : integer := 16
    );
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
    generic(
        EL_SIZE : integer := 16 
    );
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


    signal W_internal           : std_logic_vector ((EL_SIZE*W_IDX)-1 downto 0) := (others => '0');
    signal A_internal           : std_logic_vector ((EL_SIZE*A_IDX)-1 downto 0) := (others => '0');
    
    signal valid_internal       : std_logic := '0';
    signal tlast_internal       : std_logic := '0';

    type multi_array_type is array (0 to (W_IDX-1)) of std_logic_vector (EL_SIZE-1 downto 0);
    signal multi_array          : multi_array_type := (others => (others => '0'));
    signal multi_valid          : std_logic := '0';
    signal multi_tlast          : std_logic := '0';

    signal adder_valid          : std_logic := '0';
    signal adder_tlast          : std_logic := '0';
    signal adder_data           : std_logic_vector (EL_SIZE-1 downto 0) := (others => '0');

    signal accum_valid          : std_logic := '0';
    signal accum_tlast          : std_logic := '0';
    signal accum_data           : std_logic_vector (EL_SIZE-1 downto 0) := (others => '0');


begin

    MAIN_PROC : process (clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then

                W_internal <= (others => '0');
                A_internal <= (others => '0');

                valid_internal <= '0';
                tlast_internal   <= '0';

                Cout        <= (others => '0');
                Cvalid      <= '0';
                Ctlast      <= '0';

            else

                if accum_valid = '1' then
                    Cout <= accum_data;
                    Cvalid <= '1';
                    Ctlast <= accum_tlast;
                else
                    Cout <= (others => '0');
                    Cvalid <= '0';
                    Ctlast <= '0';
                end if;
                
                if valid = '1' then

                    W_internal          <= W_in;
                    valid_internal      <= '1';
                    tlast_internal      <= tlast_in;

                    for i in 0 to (W_IDX-1) loop
                        -- Little-endian gather: index value k selects activation element k,
                        -- which (matching HBM/AXI byte order) lives at A_in[EL_SIZE*k+EL_SIZE-1 : EL_SIZE*k].
                        A_internal ((EL_SIZE*W_IDX)-(EL_SIZE*i)-1 downto EL_SIZE*W_IDX-(EL_SIZE*(i+1)))
                            <= A_in (EL_SIZE*(to_integer(unsigned(Indices((IND_NUM-1)-(i*(IND_NUM/W_IDX)) downto IND_NUM-((i+1)*(IND_NUM/W_IDX)))))+1)-1
                                     downto
                                     EL_SIZE*to_integer(unsigned(Indices((IND_NUM-1)-(i*(IND_NUM/W_IDX)) downto IND_NUM-((i+1)*(IND_NUM/W_IDX))))));
                    end loop;

                else

                    valid_internal      <= '0';
                    -- W_internal, A_internal, tlast_internal:
                    -- intentionally not assigned so they hold their last value.

                end if;
            end if;
        end if;
    end process MAIN_PROC;

    MULTIPLIERS : for i in 0 to (W_IDX-1) generate
        
        MULTIPLIER_FIRST : if i = 0 generate

            MULTIPLIER_INST_FIRST : multiplier_wrapper
            generic map(
                EL_SIZE => EL_SIZE
            )
            port map(
                aclk                    => clk,          
                aresetn                 => resetn,
                s_axis_a_tvalid         => valid_internal,
                s_axis_a_tdata          => W_internal ((EL_SIZE*(i+1))-1 downto EL_SIZE*i),
                s_axis_b_tvalid         => valid_internal,
                s_axis_b_tdata          => A_internal ((EL_SIZE*(i+1))-1 downto EL_SIZE*i),
                s_axis_b_tlast          => tlast_internal,

                m_axis_result_tvalid    => multi_valid,
                m_axis_result_tdata     => multi_array(i),
                m_axis_result_tlast     => multi_tlast
            );
            end generate;

        MULTIPLIER_OTHER : if i /= 0 generate
            
            MULTIPLIER_INST_SECOND : multiplier_wrapper
            generic map(
                EL_SIZE => EL_SIZE
            )
            port map(
                aclk                    => clk,           
                aresetn                 => resetn,
                s_axis_a_tvalid         => valid_internal,
                s_axis_a_tdata          => W_internal ((EL_SIZE*(i+1))-1 downto EL_SIZE*i),
                s_axis_b_tvalid         => valid_internal,
                s_axis_b_tdata          => A_internal ((EL_SIZE*(i+1))-1 downto EL_SIZE*i),
                s_axis_b_tlast          => tlast_internal,

                m_axis_result_tvalid    => open, 
                m_axis_result_tdata     => multi_array(i),
                m_axis_result_tlast     => open  
            );
        end generate;
    end generate;
    
    ADDER_INSTANCE : adder_wrapper
    generic map(
        EL_SIZE => EL_SIZE
    )
    port map(
        aclk                    => clk,
        aresetn                 => resetn,
        s_axis_a_tvalid         => multi_valid,
        s_axis_a_tdata          => multi_array(0),
        s_axis_b_tvalid         => multi_valid,
        s_axis_b_tdata          => multi_array(1),
        s_axis_b_tlast          => multi_tlast,

        m_axis_result_tvalid    => adder_valid,
        m_axis_result_tdata     => adder_data,
        m_axis_result_tlast     => adder_tlast
    );

    ACCUM_INSTANCE : accumulator_wrapper
    generic map(
        EL_SIZE => EL_SIZE
    )
    port map(
        aclk                    => clk,
        aresetn                 => resetn,
        s_axis_a_tvalid         => adder_valid,
        s_axis_a_tdata          => adder_data,
        s_axis_a_tlast          => adder_tlast,
        m_axis_result_tvalid    => accum_valid,
        m_axis_result_tdata     => accum_data,
        m_axis_result_tlast     => accum_tlast
    );
end architecture c_block_arch;
