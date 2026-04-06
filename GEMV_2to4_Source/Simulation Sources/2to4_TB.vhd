library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use std.textio.all;
use IEEE.std_logic_textio.all;

entity two2four_file_TB is
end entity two2four_file_TB;

architecture two2four_arch_TB of two2four_file_TB is

    component two2four is
        generic(
            EL_SIZE     : integer := 16;
            BUS_EL      : integer := 8;
            A_IDX       : integer := 2;
            B_IDX       : integer := 4;
            IND_NUM     : integer := 3;
            A_ROWS      : integer := 16;
            B_COLS      : integer := 16
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;

            B_in        : in std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
            B_valid_in  : in std_logic;
            tlast_in    : in std_logic;

            A_in        : in std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
            A_valid     : in std_logic;
            
            indices     : in std_logic_vector (96-1 downto 0);
            ind_valid   : in std_logic;

            Cout        : out std_logic_vector (EL_SIZE-1 downto 0);
            Cvalid      : out std_logic;
            Ctlast      : out std_logic
        );
    end component;

    constant clk_period : time := 10 ns;

    -- Signals
    signal clk         : std_logic := '0';
    signal resetn      : std_logic := '0';
    
    signal B_in        : std_logic_vector(127 downto 0) := (others => '0');
    signal B_valid_in  : std_logic := '0';
    signal tlast_in    : std_logic := '0';
    
    signal A_in        : std_logic_vector(127 downto 0) := (others => '0');
    signal A_valid     : std_logic := '0';
    
    signal indices     : std_logic_vector(95 downto 0) := (others => '0');
    signal ind_valid   : std_logic := '0';

    signal Cout        : std_logic_vector(15 downto 0);
    signal Cvalid      : std_logic;
    signal Ctlast      : std_logic;

    -- File Declarations
    file file_A       : text open read_mode is "A_hex.txt";
    file file_B       : text open read_mode is "B_hex.txt";
    file file_Indices : text open read_mode is "indices_hex.txt";
    file file_Output  : text open write_mode is "Output_Simulation.txt";

begin

    -- Instantiate Device Under Test (DUT)
    DUT: two2four
    port map (
        clk         => clk,
        resetn      => resetn,
        
        B_in        => B_in,
        B_valid_in  => B_valid_in,
        tlast_in    => tlast_in,
        
        A_in        => A_in,
        A_valid     => A_valid,
        
        indices     => indices,
        ind_valid   => ind_valid,
        
        Cout        => Cout,
        Cvalid      => Cvalid,
        Ctlast      => Ctlast
    );

    -- Clock Generation Process
    CLK_GEN : process
    begin
        clk <= '0';
        wait for clk_period / 2;
        clk <= '1';
        wait for clk_period / 2;
    end process;

    -- Reset Process
    RESET_PROC : process
    begin
        resetn <= '0';
        wait for clk_period * 5;
        resetn <= '1';
        wait;
    end process;

    -----------------------------------------------------------
    -- Read Vector B File
    -----------------------------------------------------------
    READ_B : process
        variable v_line_B : line;
        variable v_data_B : std_logic_vector(127 downto 0);
    begin
        B_in <= (others => '0');
        B_valid_in <= '0';
        tlast_in <= '0';
        
        -- Wait for reset to finish
        wait until resetn = '1';
        wait for clk_period;
        
        while not endfile(file_B) loop
            wait until falling_edge(clk);
            readline(file_B, v_line_B);
            hread(v_line_B, v_data_B);
            
            B_in <= v_data_B;
            B_valid_in <= '1';
            
            -- Assert tlast_in if this is the last line of the file
            if endfile(file_B) then
                tlast_in <= '1';
            else
                tlast_in <= '0';
            end if;
        end loop;
        
        wait until falling_edge(clk);
        B_valid_in <= '0';
        tlast_in <= '0';
        wait;
    end process;

    -----------------------------------------------------------
    -- Read Matrix A File
    -----------------------------------------------------------
    READ_A : process
        variable v_line_A : line;
        variable v_data_A : std_logic_vector(127 downto 0);
    begin
        A_in <= (others => '0');
        A_valid <= '0';
        
        wait until resetn = '1';
        wait for clk_period;
        
        while not endfile(file_A) loop
            wait until falling_edge(clk);
            readline(file_A, v_line_A);
            hread(v_line_A, v_data_A);
            
            A_in <= v_data_A;
            A_valid <= '1';
        end loop;
        
        wait until falling_edge(clk);
        A_valid <= '0';
        wait;
    end process;

    -----------------------------------------------------------
    -- Read Indices File
    -----------------------------------------------------------
    READ_INDICES : process
        variable v_line_I : line;
        variable v_data_I : std_logic_vector(95 downto 0);
    begin
        indices <= (others => '0');
        ind_valid <= '0';
        
        wait until resetn = '1';
        wait for clk_period;
        
        while not endfile(file_Indices) loop
            wait until falling_edge(clk);
            readline(file_Indices, v_line_I);
            hread(v_line_I, v_data_I);
            
            indices <= v_data_I;
            ind_valid <= '1';
        end loop;
        
        wait until falling_edge(clk);
        ind_valid <= '0';
        wait;
    end process;

    -----------------------------------------------------------
    -- Write Output File
    -----------------------------------------------------------
    WRITE_OUTPUT : process(clk)
        variable v_line_out : line;
    begin
        if rising_edge(clk) then
            -- Only write to the output file when the result is valid
            if Cvalid = '1' then
                hwrite(v_line_out, Cout);
                writeline(file_Output, v_line_out);
            end if;
        end if;
    end process;

end architecture two2four_arch_TB;