library IEEE;
use IEEE.std_logic_1164.all;

entity accumulator_wrapper is
    port(
        aclk                    : in std_logic;
        aresetn                 : in std_logic;
        s_axis_a_tvalid         : in std_logic;
        s_axis_a_tdata          : in std_logic_vector (16-1 downto 0);
        s_axis_a_tlast          : in std_logic;
        m_axis_result_tvalid    : out std_logic;
        m_axis_result_tdata     : out std_logic_vector (16-1 downto 0);
        m_axis_result_tlast     : out std_logic
    );
end entity accumulator_wrapper;

architecture accumulator_wrapper_arch of accumulator_wrapper is

    component Accumulator is
    port(
        aclk                    : in std_logic;
        aresetn                 : in std_logic;
        s_axis_a_tvalid         : in std_logic;
        s_axis_a_tdata          : in std_logic_vector (16-1 downto 0);
        s_axis_a_tlast          : in std_logic;
        m_axis_result_tvalid    : out std_logic;
        m_axis_result_tdata     : out std_logic_vector (16-1 downto 0);
        m_axis_result_tlast     : out std_logic
    );
    end component Accumulator;

begin

    ACCUMULATOR_INST : Accumulator
    port map (  
        aclk                    => aclk,
        aresetn                 => aresetn,
        s_axis_a_tvalid         => s_axis_a_tvalid,
        s_axis_a_tdata          => s_axis_a_tdata,
        s_axis_a_tlast          => s_axis_a_tlast,
        m_axis_result_tvalid    => m_axis_result_tvalid,
        m_axis_result_tdata     => m_axis_result_tdata,
        m_axis_result_tlast     => m_axis_result_tlast
    );

end architecture accumulator_wrapper_arch;