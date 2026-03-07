library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity bus2fifo_TB is
end entity bus2fifo_TB;

architecture bus2fifoTB_arch of bus2fifo_TB is

    component bus2fifo is
        generic(
            EL_NUM      : integer := 32;    -- Elements of Input Bus
            EL_WIDTH    : integer := 16;    -- Width of each element
            DIM         : integer := 32     -- Number of FIFO Modules and DIM of Matrix
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;

            valid_in    : in std_logic;
            data_in     : in std_logic_vector ((EL_NUM * EL_WIDTH)-1 downto 0);
            rd_en_i     : in std_logic_vector (DIM-1 downto 0);    -- Read Enable per FIFO
            
            valid_out   : out std_logic_vector (DIM-1 downto 0);   -- Valid out flag for each FIFO
            empty_i     : out std_logic_vector (DIM-1 downto 0);   -- Empty flag for each FIFO
            data_out    : out std_logic_vector ((DIM*EL_WIDTH)-1 downto 0)
        );

    end component bus2fifo;

    constant C_EL_NUM   : integer := 4;
    constant C_EL_WIDTH : integer := 16;
    constant C_DIM      : integer := 8;

    signal clk       : std_logic := '0';
    signal resetn    : std_logic := '0';
    signal valid_in  : std_logic := '0';
    signal data_in   : std_logic_vector((C_EL_NUM * C_EL_WIDTH)-1 downto 0) := (others => '0');
    signal rd_en_i   : std_logic_vector(C_DIM-1 downto 0) := (others => '0');
    
    signal valid_out : std_logic_vector(C_DIM-1 downto 0);
    signal empty_i   : std_logic_vector(C_DIM-1 downto 0);
    signal data_out  : std_logic_vector((C_DIM * C_EL_WIDTH)-1 downto 0);

    constant CLK_PERIOD : time := 10 ns;

begin

    DUT: bus2fifo
        generic map (
            EL_NUM   => C_EL_NUM,
            EL_WIDTH => C_EL_WIDTH,
            DIM      => C_DIM
        )
        port map (
            clk       => clk,
            resetn    => resetn,
            valid_in  => valid_in,
            data_in   => data_in,
            rd_en_i   => rd_en_i,
            valid_out => valid_out,
            empty_i   => empty_i,
            data_out  => data_out
        );

    CLK_PROC : process
    begin
        clk <= '0';
        wait for CLK_PERIOD/2;
        clk <= '1';
        wait for CLK_PERIOD/2;
    end process CLK_PROC;

    STIMULUS: process
        variable test_data_var : std_logic_vector((C_EL_NUM * C_EL_WIDTH)-1 downto 0);
    begin
        
        resetn <= '0';
        valid_in <= '0';
        rd_en_i <= (others => '0');
        wait for CLK_PERIOD * 10;
        
        resetn <= '1';
        
        wait for CLK_PERIOD * 20;

        wait until rising_edge(clk); 
        valid_in <= '1';
        for i in 0 to C_EL_NUM-1 loop
            test_data_var(((i+1)*C_EL_WIDTH)-1 downto i*C_EL_WIDTH) := x"AAAA";
        end loop;
        data_in <= test_data_var;
        
        wait until rising_edge(clk);
        valid_in <= '1';
        for i in 0 to C_EL_NUM-1 loop
            test_data_var(((i+1)*C_EL_WIDTH)-1 downto i*C_EL_WIDTH) := x"BBBB";
        end loop;
        data_in <= test_data_var;

        wait until rising_edge(clk);
        valid_in <= '0';
        data_in <= (others => '0');
        
        wait for CLK_PERIOD * 5;
        
        wait until rising_edge(clk);
        rd_en_i <= (others => '1');

        wait until rising_edge(clk);
        rd_en_i <= (others => '0');

        wait;
        
    end process STIMULUS;

end architecture bus2fifoTB_arch;