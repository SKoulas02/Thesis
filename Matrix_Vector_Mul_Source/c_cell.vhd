library ieee;
use ieee.std_logic_1164.all;

entity c_cell is
    port (
        clk     : in std_logic;
        avalid  : in std_logic;
        bvalid  : in std_logic;
        resetn  : in std_logic;
        start   : in std_logic;

        Ain     : in std_logic_vector (15 downto 0);
        Bin     : in std_logic_vector (15 downto 0);

        Bout    : out std_logic_vector (15 downto 0);
        Ci      : out std_logic_vector (15 downto 0);


        tempCin         : out std_logic_vector (15 downto 0);
        tempCvalid      : out std_logic;
        tempCacc        : out std_logic_vector (15 downto 0);
        tempCvalidint   : out std_logic
        
    );
end entity c_cell;

architecture c_cell_arch of c_cell is

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

    signal Breg     : std_logic_vector (15 downto 0);
    signal Cin      : std_logic_vector (15 downto 0) := x"0000";
    signal Cacc     : std_logic_vector (15 downto 0);
    signal cvalidint: std_logic;
    signal cvalidout: std_logic;
    signal cvalid   : std_logic;
    signal clken    : std_logic := '1';
    signal startint : std_logic;

begin

    MUL_ACCUM : Accum_Mul_bf16
        port map(
            s_axis_a_tdata          => Ain,
            s_axis_a_tvalid         => avalid,
            s_axis_b_tdata          => Bin,
            s_axis_b_tvalid         => bvalid,
            s_axis_c_tdata          => Cin,
            s_axis_c_tvalid         => cvalid,

            aclk                    => clk,
            aclken                  => clken,
            aresetn                 => resetn,

            m_axis_result_tdata     => Cacc,
            m_axis_result_tvalid    => cvalidout
        );
    

    process(cvalidout)
    begin
        if rising_edge(cvalidout) then
            cvalidint <= '1';
        else
            cvalidint <= '0';
        end if;
    end process;

    process(start)
    begin
        if rising_edge(start) then
            startint <= '1';
        else
            startint <= '0';
        end if;
    end process;

    cvalid <= cvalidint OR startint;

    process(cvalidout)
    begin
        if rising_edge(cvalidout) then
            if resetn = '1' then
                Cin <= Cacc;
            end if;
        end if;
    end process;

    Ci <= Cin;
    Bout <= Bin;

-- Temp
    tempCin         <= Cin;
    tempCvalid      <= cvalid;
    tempCacc        <= Cacc;
    tempCvalidint   <= cvalidout;

end architecture c_cell_arch;