library ieee;
use ieee.std_logic_1164.all;

entity c_cell_TB is
end c_cell_TB;

architecture c_cell_TB_arch of c_cell_TB is

    component c_cell is
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
    end component c_cell;

    -- Signals and such 
    signal resetn   : std_logic;
    signal clk      : std_logic;
    signal avalid   : std_logic;
    signal bvalid   : std_logic;
    signal start    : std_logic;
    
    signal Ain  : std_logic_vector (15 downto 0);
    signal Bin  : std_logic_vector (15 downto 0);
    signal Bout : std_logic_vector (15 downto 0);
    signal Ci   : std_logic_vector (15 downto 0);
    
    -- Temporary

    signal tempCin,tempCacc : std_logic_vector (15 downto 0);
    signal tempCvalid,tempCvalidint : std_logic;


    constant TIME_DELAY : time := 10 ns; 

begin

    DUT : c_cell
        port map(
            clk     => clk,
            resetn  => resetn,
            avalid  => avalid,
            bvalid  => bvalid,
            start   => start,
            Ain     => Ain,
            Bin     => Bin,
            Bout    => Bout,
            Ci      => Ci,
            tempCin => tempCin,
            tempCacc=> tempCacc,
            tempCvalid=> tempCvalid,
            tempCvalidint=> tempCvalidint
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
        resetn  <= '0';
        avalid  <= '0';
        bvalid  <= '0';
        start   <= '0';

        Ain     <= x"3F80"; -- Value of 1.0 in bf16
        Bin     <= x"3F80";
        
        wait for (5*TIME_DELAY);
        
        resetn  <= '1';
        start   <= '1';
        avalid  <= '1';
        bvalid  <= '1';
        
        wait for (TIME_DELAY);
        Bin     <= x"4000";
        wait for (TIME_DELAY);
        Bin     <= x"4040";
        wait for (TIME_DELAY);
        Bin     <= x"4080";
        wait;

    end process;

end architecture c_cell_TB_arch;