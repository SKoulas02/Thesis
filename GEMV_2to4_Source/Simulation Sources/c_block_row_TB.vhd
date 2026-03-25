library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity c_block_row_TB is
end entity c_block_row_TB;

architecture c_row_arch_TB of c_block_row_TB is

    component c_block_row is
        generic(
            EL_SIZE     : integer := 16;
            A_IDX       : integer := 2;
            B_IDX       : integer := 4;
            BUS_EL      : integer := 8;
            IND_NUM     : integer := 3
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;
            
            B_vector_in : in std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
            B_valid_in  : in std_logic;
            tlast_in    : in std_logic;

            A_row       : in std_logic_vector ((BUS_EL*EL_SIZE/2)-1 downto 0);
            A_valid     : in std_logic;
            Indices     : in std_logic_vector ((2*IND_NUM)-1 downto 0);
            
            B_vector_out: out std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
            B_valid_out : out std_logic;
            tlast_out   : out std_logic;

            Cout        : out std_logic_vector ((2*EL_SIZE)-1 downto 0);
            Cvalid      : out std_logic;
            Ctlast      : out std_logic
        );
    end component;

    
    constant EL_SIZE  : integer := 16;
    constant A_IDX    : integer := 2;
    constant B_IDX    : integer := 4;
    constant BUS_EL   : integer := 8;
    constant IND_NUM  : integer := 3;
    
    constant CLK_PERIOD : time := 10 ns;

    signal clk          : std_logic := '0';
    signal resetn       : std_logic := '0';
    
    signal B_vector_in  : std_logic_vector((BUS_EL*EL_SIZE)-1 downto 0) := (others => '0');
    signal B_valid_in   : std_logic := '0';
    signal tlast_in     : std_logic := '0';

    signal A_row        : std_logic_vector((BUS_EL*EL_SIZE/2)-1 downto 0) := (others => '0');
    signal A_valid      : std_logic := '0';
    signal Indices      : std_logic_vector((2*IND_NUM)-1 downto 0) := (others => '0');
    
    signal B_vector_out : std_logic_vector((BUS_EL*EL_SIZE)-1 downto 0);
    signal B_valid_out  : std_logic;
    signal tlast_out    : std_logic;

    signal Cout         : std_logic_vector((2*EL_SIZE)-1 downto 0);
    signal Cvalid       : std_logic;
    signal Ctlast       : std_logic;

begin

    DUT: c_block_row
        generic map (
            EL_SIZE => EL_SIZE,
            A_IDX   => A_IDX,
            B_IDX   => B_IDX,
            BUS_EL  => BUS_EL,
            IND_NUM => IND_NUM
        )
        port map (
            clk          => clk,
            resetn       => resetn,
            B_vector_in  => B_vector_in,
            B_valid_in   => B_valid_in,
            tlast_in     => tlast_in,
            A_row        => A_row,
            A_valid      => A_valid,
            Indices      => Indices,
            B_vector_out => B_vector_out,
            B_valid_out  => B_valid_out,
            tlast_out    => tlast_out,
            Cout         => Cout,
            Cvalid       => Cvalid,
            Ctlast       => Ctlast
        );

    CLK_GEN : process
    begin
        clk <= '0';
        wait for CLK_PERIOD/2;
        clk <= '1';
        wait for CLK_PERIOD/2;
    end process;

    STIMULUS : process
    begin
        
        resetn <= '0';
        wait for CLK_PERIOD * 10;
        resetn <= '1';
        wait for CLK_PERIOD * 10;

        Indices <= "010" & "001"; -- Block 1 uses index 2, Block 0 uses index 1
        
        A_row   <=  x"3F80" & x"3F80" & -- Block 1
                    x"3F80" & x"3F80";  -- Block 0
        A_valid <= '1';

        B_vector_in <= x"4000" & x"4000" & x"4000" & x"4000" &  -- Block 1
                       x"4000" & x"4000" & x"4000" & x"4000";   -- Block 0
        B_valid_in  <= '1';
        tlast_in    <= '0';

        wait for CLK_PERIOD;
        
        Indices <= "101" & "101"; -- Block 1 uses index 2, Block 0 uses index 1
        
        A_row   <=  x"4000" & x"4000" & -- Block 1
                    x"4000" & x"4000";  -- Block 0
        A_valid <= '1';

        B_vector_in <= x"4040" & x"40A0" & x"4000" & x"4000" &  -- Block 1
                       x"4040" & x"40A0" & x"4000" & x"4000";   -- Block 0
        B_valid_in  <= '1';

        tlast_in <= '1';
        
        wait for CLK_PERIOD;
        
        A_valid <= '0';
        B_valid_in <= '0';
        tlast_in <= '0';

        wait;
    end process;

end architecture c_row_arch_TB;