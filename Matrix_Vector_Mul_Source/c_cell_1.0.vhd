library ieee;
use ieee.std_logic_1164.all;

entity c_cell is
    generic(
        EL_WIDTH    : integer := 16
    );
    port(
        clk     : in std_logic;
        resetn  : in std_logic;
        
        Ain     : in std_logic_vector (EL_WIDTH-1 downto 0);
        avalid  : in std_logic;
        
        Bin     : in std_logic_vector (EL_WIDTH-1 downto 0);
        bvalid  : in std_logic;
        tlast_in: in std_logic;

        Aout        : out std_logic_vector (EL_WIDTH-1 downto 0);
        avalidout   : out std_logic;

        Bout        : out std_logic_vector (EL_WIDTH-1 downto 0);
        bvalidout   : out std_logic;
        tlast_out   : out std_logic;
        
        Ci      : out std_logic_vector (EL_WIDTH-1 downto 0);
        Cvalid  : out std_logic;
        Ctlast  : out std_logic
    );
end entity c_cell;

architecture c_cell_arch of c_cell is

    component Accumulator is
        port(
            aclk                    : in std_logic;
            aresetn                 : in std_logic;

            s_axis_a_tvalid         : in std_logic;
            s_axis_a_tdata          : in std_logic_vector (EL_WIDTH-1 downto 0);
            s_axis_a_tlast          : in std_logic;

            m_axis_result_tvalid    : out std_logic;
            m_axis_result_tdata     : out std_logic_vector (EL_WIDTH-1 downto 0);
            m_axis_result_tlast     : out std_logic
        );
    end component Accumulator;

    component Multiplier is
        port(
            aclk                    : in std_logic;
            aresetn                 : in std_logic;

            s_axis_a_tvalid         : in std_logic;
            s_axis_a_tdata          : in std_logic_vector (EL_WIDTH-1 downto 0);
            s_axis_a_tlast          : in std_logic;

            s_axis_b_tvalid         : in std_logic;
            s_axis_b_tdata          : in std_logic_vector (EL_WIDTH-1 downto 0);

            m_axis_result_tvalid    : out std_logic;
            m_axis_result_tdata     : out std_logic_vector (EL_WIDTH-1 downto 0);
            m_axis_result_tlast     : out std_logic
        );
    end component Multiplier;
   

    signal valid_mul    : std_logic;
    signal data_mul     : std_logic_vector (EL_WIDTH-1 downto 0);
    signal tlast_mul    : std_logic;

    signal C_internal   : std_logic_vector (EL_WIDTH-1 downto 0);
    signal C_tlast_int  : std_logic := '0';
    signal C_valid_int  : std_logic;

begin

    MAIN_PROC : process (clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then
                
                Cvalid <= '0';
                Ctlast <= '0';
                Ci     <= (others => '0');
                Aout   <= (others => '0');
                Bout   <= (others => '0');
                avalidout   <= '0';
                bvalidout   <= '0';
                tlast_out   <= '0';
                

            else
                
                if C_tlast_int = '1' then
                    Ci  <= C_internal;
                    Ctlast  <= C_tlast_int;
                else
                    Ctlast  <= '0';
                end if;
                
                Cvalid      <= C_valid_int;
                tlast_out   <= tlast_in;
                Aout        <= Ain;
                Bout        <= Bin;
                avalidout   <= avalid;
                bvalidout   <= bvalid;

            end if;
        end if;
    end process;
    
    MULTI_INSTANCE : Multiplier
    port map(
        aclk                    => clk,
        aresetn                 => resetn,

        s_axis_a_tvalid         => avalid,
        s_axis_a_tdata          => Ain,
        s_axis_a_tlast          => tlast_in,

        s_axis_b_tvalid         => bvalid,
        s_axis_b_tdata          => Bin,

        m_axis_result_tvalid    => valid_mul,
        m_axis_result_tdata     => data_mul,
        m_axis_result_tlast     => tlast_mul
    );

    ACCUM_INSTANCE : Accumulator
    port map(
        aclk                    => clk,
        aresetn                 => resetn,

        s_axis_a_tvalid         => valid_mul,
        s_axis_a_tdata          => data_mul,
        s_axis_a_tlast          => tlast_mul,

        m_axis_result_tvalid    => C_valid_int,
        m_axis_result_tdata     => C_internal,
        m_axis_result_tlast     => C_tlast_int
    );

end architecture c_cell_arch;