library ieee;
use ieee.std_logic_1164.all;

entity add_mul_TB is
end add_mul_TB;

architecture add_mul_TB_arch of add_mul_TB is

    component Accum_Mul_bf16 is
        port(
            s_axis_a_tdata      : in std_logic_vector (15 downto 0);
            s_axis_a_tvalid     : in std_logic;
            s_axis_b_tdata      : in std_logic_vector (15 downto 0);
            s_axis_b_tvalid     : in std_logic;
            s_axis_c_tdata      : in std_logic_vector (15 downto 0);
            s_axis_c_tvalid     : in std_logic;

            aclk                : in std_logic;
            aclken              : in std_logic;
            aresetn             : in std_logic;

            m_axis_result_tdata : out std_logic_vector (15 downto 0);
            m_axis_result_tvalid: out std_logic
        );
    end component Accum_Mul_bf16;

    -- Signals and such 
    signal reset    : std_logic;
    signal clk      : std_logic;
    signal clken    : std_logic;
    signal avalid   : std_logic;
    signal bvalid   : std_logic;
    signal cvalid   : std_logic;
    signal cvalidout: std_logic;

    signal A    : std_logic_vector (15 downto 0);
    signal B    : std_logic_vector (15 downto 0);
    signal Cin  : std_logic_vector (15 downto 0);
    signal Cout : std_logic_vector (15 downto 0);

    constant TIME_DELAY : time := 10 ns; 

begin

    DUT : Accum_Mul_bf16
        port map(
            s_axis_a_tdata          => A,
            s_axis_a_tvalid         => avalid,
            s_axis_b_tdata          => B,
            s_axis_b_tvalid         => bvalid,
            s_axis_c_tdata          => Cin,
            s_axis_c_tvalid         => cvalid,

            aclk                    => clk,
            aclken                  => clken,
            aresetn                 => reset,

            m_axis_result_tdata     => Cout,
            m_axis_result_tvalid    => cvalidout
        );


    ClockGen : process
    begin
        clk <= '1';
        wait for (TIME_DELAY/2);
        clk <= '0';
        wait for (TIME_DELAY/2);
    end process;


    STIMULUS : process
    begin
        reset   <= '0';
        clken   <= '1';
        avalid  <= '0';
        bvalid  <= '0';
        cvalid  <= '0';

        A       <= x"3F80"; -- Value of 1.0 in bf16
        B       <= x"3F80";
        Cin     <= x"4000";

        wait for (5*TIME_DELAY);
        
        reset <= '1';
        
        wait for (2*TIME_DELAY);

        avalid <= '1';
        bvalid <= '1';
        cvalid <= '1';
        wait for (TIME_DELAY);
        Cin     <= x"4040";

        wait;

    end process;

end architecture add_mul_TB_arch;