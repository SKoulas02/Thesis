library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;

entity adder_tree is
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
end entity adder_tree;

architecture adder_tree_arch of adder_tree is

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

    constant LEVELS : integer := integer(ceil(log2(real(EL_NUM))));    -- Number of Levels in adder_wrapper Tree

    type tree_data_type is array (0 to LEVELS) of std_logic_vector ((EL_SIZE*EL_NUM)-1 downto 0);
    type tree_valid_type is array (0 to LEVELS) of std_logic_vector (EL_NUM-1 downto 0);
    

    signal tree_data : tree_data_type := (others => (others => '0'));
    signal tree_valid : tree_valid_type := (others => (others => '0'));
    signal tlast_internal : std_logic_vector (LEVELS downto 0) := (others => '0');

begin

    tlast_internal(0) <= tlast;


    PACK_INPUT : for i in 0 to EL_NUM-1 generate
        tree_data(0)((i+1)*EL_SIZE-1 downto i*EL_SIZE) <= in_elements((i+1)*EL_SIZE-1 downto i*EL_SIZE);
        tree_valid(0)(i) <= in_valid;
    end generate;

    GEN_LEVELS : for i in 0 to LEVELS-1 generate
        GEN_ADDERS : for j in 0 to (EL_NUM/(2**(i+1)))-1 generate
            TLAST_ADDER : if j = 0 generate
                adder_inst : adder_wrapper
                port map(
                    aclk                    => clk,
                    aresetn                 => resetn,

                    s_axis_a_tvalid         => tree_valid(i)(2*j),
                    s_axis_a_tdata          => tree_data(i)((2*j+1)*EL_SIZE-1 downto (2*j)*EL_SIZE),

                    s_axis_b_tvalid         => tree_valid(i)(2*j+1),
                    s_axis_b_tdata          => tree_data(i)((2*j+2)*EL_SIZE-1 downto (2*j+1)*EL_SIZE),
                    s_axis_b_tlast          => tlast_internal(i),

                    m_axis_result_tvalid    => tree_valid(i+1)(j),
                    m_axis_result_tdata     => tree_data(i+1)((j+1)*EL_SIZE-1 downto j*EL_SIZE),
                    m_axis_result_tlast     => tlast_internal(i+1)
                );
            end generate;

            OTHER_ADDERS : if j > 0 generate
                adder_inst : adder_wrapper
                port map(
                    aclk                    => clk,
                    aresetn                 => resetn,

                    s_axis_a_tvalid         => tree_valid(i)(2*j),
                    s_axis_a_tdata          => tree_data(i)((2*j+1)*EL_SIZE-1 downto (2*j)*EL_SIZE),

                    s_axis_b_tvalid         => tree_valid(i)(2*j+1),
                    s_axis_b_tdata          => tree_data(i)((2*j+2)*EL_SIZE-1 downto (2*j+1)*EL_SIZE),
                    s_axis_b_tlast          => tlast_internal(i),

                    m_axis_result_tvalid    => tree_valid(i+1)(j),
                    m_axis_result_tdata     => tree_data(i+1)((j+1)*EL_SIZE-1 downto j*EL_SIZE),
                    m_axis_result_tlast     => open
                );
            end generate;
        end generate GEN_ADDERS;
    end generate GEN_LEVELS;

    out_elements <= tree_data(LEVELS)(EL_SIZE-1 downto 0);
    out_valid <= tree_valid(LEVELS)(0);
    tlast_out <= tlast_internal(LEVELS);

end architecture adder_tree_arch;