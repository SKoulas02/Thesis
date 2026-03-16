library ieee;
use ieee.std_logic_1164.all;

entity c_cell_TB is
end c_cell_TB;

architecture c_cell_TB_arch of c_cell_TB is

    component c_cell is
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
    end component c_cell;

    constant el_width   : integer := 16;
    constant clk_period : time := 10 ns;

    signal clk          : std_logic := '0';
    signal resetn       : std_logic := '0';

    signal Ain          : std_logic_vector(EL_WIDTH-1 downto 0) := (others => '0');
    signal avalid       : std_logic := '0';
    signal tlast_in     : std_logic := '0';

    signal Bin          : std_logic_vector(EL_WIDTH-1 downto 0) := (others => '0');
    signal bvalid       : std_logic := '0';

    signal Aout         : std_logic_vector(EL_WIDTH-1 downto 0);
    signal avalidout    : std_logic;
    signal Bout         : std_logic_vector(EL_WIDTH-1 downto 0);
    signal bvalidout    : std_logic;
    signal tlast_out    : std_logic;
    
    signal Ci           : std_logic_vector(EL_WIDTH-1 downto 0);
    signal Cvalid       : std_logic;
    signal Ctlast       : std_logic;

begin

    DUT: c_cell
        port map(
            clk         => clk,
            resetn      => resetn,

            Ain         => Ain,
            avalid      => avalid,

            Bin         => Bin,
            bvalid      => bvalid,
            tlast_in    => tlast_in,

            Aout        => Aout,
            avalidout   => avalidout,

            Bout        => Bout,
            bvalidout   => bvalidout,
            tlast_out   => tlast_out,

            Ci          => Ci,
            Cvalid      => Cvalid,
            Ctlast      => Ctlast
        );

    CLK_GEN : process
    begin
        clk <= '0';
        wait for clk_period/2;
        clk <= '1';
        wait for clk_period/2;
    end process;

    STIMULUS: process
    begin
        resetn <= '0';
        wait for 20*clk_period;
        resetn <= '1';
        wait for 20*clk_period;

        wait until rising_edge(clk);
        Ain <= x"3F80";
        Bin <= x"3F80";
        avalid <= '0';
        bvalid <= '1';
        tlast_in <= '0';

        wait until rising_edge(clk);
        Ain <= x"4000";
        avalid <= '1';
        bvalid <= '1';
        tlast_in <= '0';

        wait until rising_edge(clk);
        Ain <= x"4000";
        Bin <= x"40C0";
        avalid <= '0';
        bvalid <= '0';
        tlast_in <= '0';

        wait until rising_edge(clk);
        Ain <= x"4000";
        Bin <= x"4000";
        avalid <= '1';
        bvalid <= '1';
        tlast_in <= '1';

        wait until rising_edge(clk);
        avalid <= '0';
        bvalid <= '0';
        tlast_in <= '0';

        wait for clk_period*30;

        wait until rising_edge(clk);
        Ain <= x"4000";
        Bin <= x"4000";
        avalid <= '0';
        bvalid <= '1';
        tlast_in <= '0';

        wait until rising_edge(clk);
        Ain <= x"4000";
        avalid <= '1';
        bvalid <= '1';
        tlast_in <= '0';

        wait until rising_edge(clk);
        Ain <= x"4000";
        Bin <= x"40C0";
        avalid <= '0';
        bvalid <= '0';
        tlast_in <= '0';

        wait until rising_edge(clk);
        Ain <= x"4000";
        Bin <= x"4000";
        avalid <= '1';
        bvalid <= '1';
        tlast_in <= '1';

        wait until rising_edge(clk);
        avalid <= '0';
        bvalid <= '0';
        tlast_in <= '0';


        wait;
    end process;

end architecture c_cell_TB_arch;