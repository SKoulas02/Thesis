library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use std.textio.all;
use IEEE.std_logic_textio.all;

entity wrapper_2to4_TB is
end entity wrapper_2to4_TB;

architecture wrapper_2to4_arch_TB of wrapper_2to4_TB is

    component wrapper_2to4 is
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
            clk         : in  std_logic;
            resetn      : in  std_logic;

            B_tdata     : in  std_logic_vector((BUS_EL*EL_SIZE)-1 downto 0);
            B_tvalid    : in  std_logic;
            B_tlast     : in  std_logic;
            B_tready    : out std_logic;

            A_tdata     : in  std_logic_vector((BUS_EL*EL_SIZE)-1 downto 0);
            A_tvalid    : in  std_logic;
            A_tready    : out std_logic;

            ind_tdata   : in  std_logic_vector(95 downto 0);
            ind_tvalid  : in  std_logic;
            ind_tready  : out std_logic;

            C_tdata     : out std_logic_vector(EL_SIZE-1 downto 0);
            C_tvalid    : out std_logic;
            C_tlast     : out std_logic;
            C_tready    : in  std_logic
        );
    end component;

    constant clk_period : time := 10 ns;

    -- Signals
    signal clk         : std_logic := '0';
    signal resetn      : std_logic := '0';

    signal B_tdata     : std_logic_vector(127 downto 0) := (others => '0');
    signal B_tvalid    : std_logic := '0';
    signal B_tlast     : std_logic := '0';
    signal B_tready    : std_logic;

    signal A_tdata     : std_logic_vector(127 downto 0) := (others => '0');
    signal A_tvalid    : std_logic := '0';
    signal A_tready    : std_logic;

    signal ind_tdata   : std_logic_vector(95 downto 0) := (others => '0');
    signal ind_tvalid  : std_logic := '0';
    signal ind_tready  : std_logic;

    signal C_tdata     : std_logic_vector(15 downto 0);
    signal C_tvalid    : std_logic;
    signal C_tlast     : std_logic;
    signal C_tready    : std_logic := '1';

    -- File Declarations
    file file_A       : text open read_mode is "A_hex.txt";
    file file_B       : text open read_mode is "B_hex.txt";
    file file_Indices : text open read_mode is "indices_hex.txt";
    file file_Output  : text open write_mode is "Output_Wrapper_Simulation.txt";

begin

    DUT: wrapper_2to4
    port map (
        clk         => clk,
        resetn      => resetn,

        B_tdata     => B_tdata,
        B_tvalid    => B_tvalid,
        B_tlast     => B_tlast,
        B_tready    => B_tready,

        A_tdata     => A_tdata,
        A_tvalid    => A_tvalid,
        A_tready    => A_tready,

        ind_tdata   => ind_tdata,
        ind_tvalid  => ind_tvalid,
        ind_tready  => ind_tready,

        C_tdata     => C_tdata,
        C_tvalid    => C_tvalid,
        C_tlast     => C_tlast,
        C_tready    => C_tready
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
        B_tdata  <= (others => '0');
        B_tvalid <= '0';
        B_tlast  <= '0';

        wait until resetn = '1';
        wait for clk_period * 10;

        while not endfile(file_B) loop
            wait until falling_edge(clk);
            readline(file_B, v_line_B);
            hread(v_line_B, v_data_B);

            B_tdata  <= v_data_B;
            B_tvalid <= '1';

            if endfile(file_B) then
                B_tlast <= '1';
            else
                B_tlast <= '0';
            end if;
        end loop;

        wait until falling_edge(clk);
        B_tvalid <= '0';
        B_tlast  <= '0';
        wait;
    end process;

    -----------------------------------------------------------
    -- Read Matrix A File
    -----------------------------------------------------------
    READ_A : process
        variable v_line_A : line;
        variable v_data_A : std_logic_vector(127 downto 0);
    begin
        A_tdata  <= (others => '0');
        A_tvalid <= '0';

        wait until resetn = '1';
        wait for clk_period * 10;

        while not endfile(file_A) loop
            readline(file_A, v_line_A);
            hread(v_line_A, v_data_A);
            wait until rising_edge(clk);
            A_tvalid <= '1';
            A_tdata  <= v_data_A;
            wait until rising_edge(clk);
            A_tvalid <= '0';
            wait until rising_edge(clk);
            A_tvalid <= '0';
            wait until rising_edge(clk);
            A_tvalid <= '0';
            wait until rising_edge(clk);
            A_tvalid <= '0';
        end loop;

        wait until falling_edge(clk);
        A_tvalid <= '0';
        wait;
    end process;

    -----------------------------------------------------------
    -- Read Indices File
    -----------------------------------------------------------
    READ_INDICES : process
        variable v_line_I : line;
        variable v_data_I : std_logic_vector(95 downto 0);
    begin
        ind_tdata  <= (others => '0');
        ind_tvalid <= '0';

        wait until resetn = '1';
        wait for clk_period * 10;

        while not endfile(file_Indices) loop
            wait until falling_edge(clk);
            readline(file_Indices, v_line_I);
            hread(v_line_I, v_data_I);

            ind_tdata  <= v_data_I;
            ind_tvalid <= '1';
        end loop;

        wait until falling_edge(clk);
        ind_tvalid <= '0';
        wait;
    end process;

    -----------------------------------------------------------
    -- Write Output File
    -----------------------------------------------------------
    WRITE_OUTPUT : process(clk)
        variable v_line_out : line;
    begin
        if rising_edge(clk) then
            if C_tvalid = '1' then
                hwrite(v_line_out, C_tdata);
                writeline(file_Output, v_line_out);
            end if;
        end if;
    end process;

end architecture wrapper_2to4_arch_TB;
