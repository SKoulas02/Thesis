library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity top_module_TB is
end entity top_module_TB;

architecture top_arch_TB of top_module_TB is

    component top_module is
       generic(
          ROWS     : integer := 1;
          COLS     : integer := 16;
          EL_WIDTH : integer := 16;
          EL_NUM   : integer := 16  
       );
       port(
          clk      : in std_logic;
          resetn   : in std_logic;
          
          a_bus    : in std_logic_vector((EL_NUM*EL_WIDTH)-1 downto 0);
          a_valid  : in std_logic;
          top_tlast: in std_logic;
          
          b_bus    : in std_logic_vector((ROWS*EL_WIDTH)-1 downto 0);
          b_valid  : in std_logic;

          c_array_out : out std_logic_vector((ROWS*COLS*EL_WIDTH)-1 downto 0);
          c_valid     : out std_logic_vector(COLS-1 downto 0);
          c_tlast     : out std_logic
       );
    end component;

    constant C_ROWS     : integer := 1;
    constant C_COLS     : integer := 16;
    constant C_EL_WIDTH : integer := 16;
    constant C_EL_NUM   : integer := 16;

    constant CLK_PERIOD : time := 10 ns; 

    signal clk         : std_logic := '0';
    signal resetn      : std_logic := '0';
    
    signal a_bus       : std_logic_vector((C_EL_NUM*C_EL_WIDTH)-1 downto 0) := (others => '0');
    signal a_valid     : std_logic := '0';
    signal top_tlast   : std_logic := '0';
    
    signal b_bus       : std_logic_vector((C_ROWS*C_EL_WIDTH)-1 downto 0) := (others => '0');
    signal b_valid     : std_logic := '0';

    signal c_array_out : std_logic_vector((C_ROWS*C_COLS*C_EL_WIDTH)-1 downto 0);
    signal c_valid     : std_logic_vector(C_COLS-1 downto 0) := (others => '0');
    signal c_tlast     : std_logic := '0';

begin

    DUT: top_module
    generic map(
        ROWS     => C_ROWS,
        COLS     => C_COLS,
        EL_WIDTH => C_EL_WIDTH,
        EL_NUM   => C_EL_NUM
    )
    port map(
        clk         => clk,
        resetn      => resetn,
        a_bus       => a_bus,
        a_valid     => a_valid,
        top_tlast   => top_tlast,
        b_bus       => b_bus,
        b_valid     => b_valid,
        c_array_out => c_array_out,
        c_valid     => c_valid,
        c_tlast     => c_tlast
    );

    CLK_GEN : process
    begin
        clk <= '0';
        wait for CLK_PERIOD/2;
        clk <= '1';
        wait for CLK_PERIOD/2;
    end process;

    STIMULUS : process
        variable var_a_bus : std_logic_vector((C_EL_NUM*C_EL_WIDTH)-1 downto 0) := (others => '0');
        variable var_b_bus : std_logic_vector((C_ROWS*C_EL_WIDTH)-1 downto 0) := (others => '0');
    begin
      
        resetn <= '0';
        a_valid <= '0';
        b_valid <= '0';
        top_tlast <= '0';
        wait for CLK_PERIOD * 20;
        
        resetn <= '1';
        wait for CLK_PERIOD * 20;

        for i in 0 to C_EL_NUM-1 loop
            var_a_bus(((i+1)*C_EL_WIDTH)-1 downto i*C_EL_WIDTH) :=  x"3F80";
        end loop;
        
        var_b_bus := x"3F80";

        a_bus   <= var_a_bus;
        b_bus   <= var_b_bus;
        a_valid <= '1';
        b_valid <= '1';
        wait for CLK_PERIOD; 

        for i in 0 to C_EL_NUM-1 loop
            var_a_bus(((i+1)*C_EL_WIDTH)-1 downto i*C_EL_WIDTH) :=  x"4000";
        end loop;
        
        var_b_bus := x"4000";

        a_bus   <= var_a_bus;
        b_bus   <= var_b_bus;
        a_valid <= '1';
        b_valid <= '1';
        top_tlast <= '1';

        wait for CLK_PERIOD; 

        top_tlast <= '0';
        a_valid <= '0';
        b_valid <= '0';
        
        wait for CLK_PERIOD*50;

        resetn <= '0';

        wait for CLK_PERIOD*20;

        resetn <= '1';
        for i in 0 to C_EL_NUM-1 loop
            var_a_bus(((i+1)*C_EL_WIDTH)-1 downto i*C_EL_WIDTH) :=  x"3F80";
        end loop;
        
        var_b_bus := x"3F80";

        a_bus   <= var_a_bus;
        b_bus   <= var_b_bus;
        a_valid <= '1';
        b_valid <= '1';
        wait for CLK_PERIOD; 

        for i in 0 to C_EL_NUM-1 loop
            var_a_bus(((i+1)*C_EL_WIDTH)-1 downto i*C_EL_WIDTH) :=  x"4000";
        end loop;
        
        var_b_bus := x"4000";

        a_bus   <= var_a_bus;
        b_bus   <= var_b_bus;
        a_valid <= '1';
        b_valid <= '1';
        top_tlast <= '1';

        wait for CLK_PERIOD; 

        top_tlast <= '0';
        a_valid <= '0';
        b_valid <= '0';

        wait;
    end process;

end architecture top_arch_TB;