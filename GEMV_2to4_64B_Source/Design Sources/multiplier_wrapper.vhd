library IEEE;
use IEEE.std_logic_1164.all;

entity multiplier_wrapper is
    port(
        aclk                    : in std_logic;
        aresetn                 : in std_logic;
        s_axis_a_tvalid         : in std_logic;
        s_axis_a_tdata          : in std_logic_vector (16-1 downto 0);
        s_axis_b_tvalid         : in std_logic;
        s_axis_b_tdata          : in std_logic_vector (16-1 downto 0);
        s_axis_b_tlast          : in std_logic;
        m_axis_result_tvalid    : out std_logic;
        m_axis_result_tdata     : out std_logic_vector (16-1 downto 0);
        m_axis_result_tlast     : out std_logic
    );
end entity multiplier_wrapper;

architecture multiplier_wrapper_arch of multiplier_wrapper is

    component Multiplier is
    port(
        aclk                    : in std_logic;
        aresetn                 : in std_logic;
        s_axis_a_tvalid         : in std_logic;
        s_axis_a_tdata          : in std_logic_vector (16-1 downto 0);
        s_axis_b_tvalid         : in std_logic;
        s_axis_b_tdata          : in std_logic_vector (16-1 downto 0);
        s_axis_b_tlast          : in std_logic;
        m_axis_result_tvalid    : out std_logic;
        m_axis_result_tdata     : out std_logic_vector (16-1 downto 0);
        m_axis_result_tlast     : out std_logic
    );
    end component Multiplier;

begin

    MULTIPLIER_INST : Multiplier
    port map (  
        aclk                    => aclk,
        aresetn                 => aresetn,
        s_axis_a_tvalid         => s_axis_a_tvalid,
        s_axis_a_tdata          => s_axis_a_tdata,
        s_axis_b_tvalid         => s_axis_b_tvalid,
        s_axis_b_tdata          => s_axis_b_tdata,
        s_axis_b_tlast          => s_axis_b_tlast,
        m_axis_result_tvalid    => m_axis_result_tvalid,
        m_axis_result_tdata     => m_axis_result_tdata,
        m_axis_result_tlast     => m_axis_result_tlast
    );

end architecture multiplier_wrapper_arch;