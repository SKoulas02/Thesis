library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity c_block_TB is
end entity c_block_TB;

architecture c_block_arch_TB of c_block_TB is

    component c_block is
        generic(
            EL_SIZE : integer := 16;
            A_IDX   : integer := 2;
            B_IDX   : integer := 4
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;
            A_in        : in std_logic_vector ((EL_SIZE*A_IDX)-1 downto 0);
            A_valid     : in std_logic;
            Indeces     : in std_logic_vector (B_IDX-1 downto 0);
            B_in        : in std_logic_vector ((EL_SIZE*B_IDX)-1 downto 0);
            B_valid     : in std_logic;
            tlast_in    : in std_logic;
            Bout        : out std_logic_vector ((EL_SIZE*B_IDX)-1 downto 0);
            tlast_out   : out std_logic;
            B_valid_out : out std_logic;
            Cout        : out std_logic_vector (EL_SIZE-1 downto 0);
            Cvalid      : out std_logic;
            Ctlast      : out std_logic
        );
    end component;

    constant C_EL_SIZE  : integer := 16;
    constant C_A_IDX    : integer := 2;
    constant C_B_IDX    : integer := 4;
    constant CLK_PERIOD : time := 10 ns;


    signal clk         : std_logic := '0';
    signal resetn      : std_logic := '0';
    
    signal A_in        : std_logic_vector((C_EL_SIZE * C_A_IDX)-1 downto 0) := (others => '0');
    signal A_valid     : std_logic := '0';
    signal Indeces     : std_logic_vector(C_B_IDX-1 downto 0) := (others => '0');
    
    signal B_in        : std_logic_vector((C_EL_SIZE * C_B_IDX)-1 downto 0) := (others => '0');
    signal B_valid     : std_logic := '0';
    signal tlast_in    : std_logic := '0';
    
    signal Bout        : std_logic_vector((C_EL_SIZE * C_B_IDX)-1 downto 0);
    signal tlast_out   : std_logic;
    signal B_valid_out : std_logic;
    
    signal Cout        : std_logic_vector(C_EL_SIZE-1 downto 0);
    signal Cvalid      : std_logic;
    signal Ctlast      : std_logic;

begin

    DUT: c_block
        generic map (
            EL_SIZE => C_EL_SIZE,
            A_IDX   => C_A_IDX,
            B_IDX   => C_B_IDX
        )
        port map (
            clk         => clk,
            resetn      => resetn,
            A_in        => A_in,
            A_valid     => A_valid,
            Indeces     => Indeces,
            B_in        => B_in,
            B_valid     => B_valid,
            tlast_in    => tlast_in,
            Bout        => Bout,
            tlast_out   => tlast_out,
            B_valid_out => B_valid_out,
            Cout        => Cout,
            Cvalid      => Cvalid,
            Ctlast      => Ctlast
        );

    CLK_PROC : process
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

        
        -- A = [2.0, 2.0] and B = [1.0, 2.0, 2.0, 1.0]
        
        -- Leftmost is MSB.
        A_in <= x"4000" & x"4000";
        B_in <= x"3F80" & x"4000" & x"4000" & x"3F80";
        
        -- Picked B[2]=2.0 and B[1]=2.0
        Indeces <= "0110";
        
        A_valid  <= '1';
        B_valid  <= '1';
        tlast_in <= '0';
        
        wait for CLK_PERIOD; 
                
        -- Picked B[3]=1.0 and B[2]=2.0
        Indeces  <= "1100"; 
        A_valid  <= '1';
        B_valid  <= '1';
        tlast_in <= '1';
        
        wait for CLK_PERIOD;
        
        A_valid  <= '0';
        B_valid  <= '0';
        tlast_in <= '0';
        A_in     <= (others => '0');
        B_in     <= (others => '0');

        wait for CLK_PERIOD*10;

        A_in <= x"4000" & x"4000";
        B_in <= x"3F80" & x"4000" & x"4000" & x"3F80";
        
        -- Picked B[2]=2.0 and B[1]=2.0
        Indeces <= "0110";
        A_valid  <= '1';
        B_valid  <= '1';
        tlast_in <= '0';
        
        wait for CLK_PERIOD;      

        -- Picked B[3]=1.0 and B[2]=2.0
        Indeces  <= "1100"; 
        A_valid  <= '1';
        B_valid  <= '1';
        tlast_in <= '1';
        
        wait for CLK_PERIOD;
        
        A_valid  <= '0';
        B_valid  <= '0';
        tlast_in <= '0';
        wait;
    end process;

end architecture c_block_arch_TB;