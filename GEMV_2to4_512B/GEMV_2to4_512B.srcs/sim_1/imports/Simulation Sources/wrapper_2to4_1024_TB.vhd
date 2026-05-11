library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;
use std.textio.all;
use IEEE.std_logic_textio.all;

entity wrapper_2to4_1024_TB is
end entity wrapper_2to4_1024_TB;

architecture wrapper_2to4_arch_TB of wrapper_2to4_1024_TB is

    component wrapper_2to4 is
        generic(
        EL_SIZE     : integer := 16;    -- Bit size of each element
        BUS_EL      : integer := 32;    -- Maximum number of elements on bus

        A_IDX       : integer := 2;     -- Number of matrix elements
        B_IDX       : integer := 4;     -- Number of vector elements
        IND_NUM     : integer := 3;     -- Number of indices Bits

        A_ROWS      : integer := 1024   -- Number of Rows of Matrix A
    );
    port(
        clk         : in  std_logic;
        resetn      : in  std_logic;

        -- B vector AXI-Stream slave
        B_tdata     : in  std_logic_vector((BUS_EL*EL_SIZE)-1 downto 0);
        B_tvalid    : in  std_logic;
        B_tlast     : in  std_logic;
        B_tready    : out std_logic;

        -- A matrix AXI-Stream slave
        A_tdata     : in  std_logic_vector((BUS_EL*EL_SIZE)-1 downto 0);
        A_tvalid    : in  std_logic;
        A_tready    : out std_logic;

        -- Indices AXI-Stream slave
        ind_tdata   : in  std_logic_vector ( (2 ** integer(floor(log2(real((B_IDX*EL_SIZE)/IND_NUM))))) * IND_NUM*BUS_EL/B_IDX -1 downto 0);
        ind_tvalid  : in  std_logic;
        ind_tready  : out std_logic;

        -- C output AXI-Stream master
        C_tdata     : out std_logic_vector(EL_SIZE-1 downto 0);
        C_tvalid    : out std_logic;
        C_tlast     : out std_logic;
        C_tready    : in  std_logic
    );
    end component wrapper_2to4;

    constant clk_period : time := 10 ns;
    constant BUS_SIZE   : integer := 512;  -- Number of bits in Bus
    constant IND_SIZE   : integer := 384;  -- Number of bits in Indices
    constant EL_SIZE    : integer := 16;   -- Bit size of each element

    -- Reduce SIM_A_ROWS (e.g. 32 or 64) for low-RAM elaboration smoke tests.
    -- Use 1024 only with xsim.elaborate.debug_level = off (Project Settings -> Simulation -> Elaboration).
    constant SIM_A_ROWS : integer := 1024;


    -- Signals
    signal clk         : std_logic := '0';
    signal resetn      : std_logic := '0';

    signal B_tdata     : std_logic_vector(BUS_SIZE-1 downto 0) := (others => '0');
    signal B_tvalid    : std_logic := '0';
    signal B_tlast     : std_logic := '0';
    signal B_tready    : std_logic;

    signal A_tdata     : std_logic_vector(BUS_SIZE-1 downto 0) := (others => '0');
    signal A_tvalid    : std_logic := '0';
    signal A_tready    : std_logic;

    signal ind_tdata   : std_logic_vector(IND_SIZE-1 downto 0) := (others => '0');
    signal ind_tvalid  : std_logic := '0';
    signal ind_tready  : std_logic;

    signal C_tdata     : std_logic_vector(EL_SIZE-1 downto 0);
    signal C_tvalid    : std_logic;
    signal C_tlast     : std_logic;
    signal C_tready    : std_logic := '1';

    -- File Declarations
    file file_A       : text open read_mode is "A_1024x512.txt";
    file file_B       : text open read_mode is "B_1024x1.txt";
    file file_Indices : text open read_mode is "indices_1024x512.txt";
    file file_Output  : text open write_mode is "Output_Simulation_1024.txt";

begin

    DUT: wrapper_2to4
    generic map (
        EL_SIZE => EL_SIZE,
        BUS_EL  => BUS_SIZE / EL_SIZE,
        A_IDX   => 2,
        B_IDX   => 4,
        IND_NUM => 3,
        A_ROWS  => SIM_A_ROWS
    )
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
        variable v_data_B : std_logic_vector(BUS_SIZE-1 downto 0);
    begin
        B_tdata  <= (others => '0');
        B_tvalid <= '0';
        B_tlast  <= '0';

        wait until resetn = '1';
        wait for clk_period * 10;

        while not endfile(file_B) loop
            readline(file_B, v_line_B);
            hread(v_line_B, v_data_B);

            wait until falling_edge(clk);
            B_tdata  <= v_data_B;
            B_tvalid <= '1';

            if endfile(file_B) then
                B_tlast <= '1';
            else
                B_tlast <= '0';
            end if;

            -- Hold until handshake completes
            wait until rising_edge(clk) and B_tready = '1';
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
        variable v_data_A : std_logic_vector(BUS_SIZE-1 downto 0);
    begin
        A_tdata  <= (others => '0');
        A_tvalid <= '0';

        wait until resetn = '1';
        wait for clk_period * 10;

        while not endfile(file_A) loop
            readline(file_A, v_line_A);
            hread(v_line_A, v_data_A);

            wait until falling_edge(clk);
            A_tvalid <= '1';
            A_tdata  <= v_data_A;

            -- Hold until handshake completes
            wait until rising_edge(clk) and A_tready = '1';
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
        variable v_data_I : std_logic_vector(IND_SIZE-1 downto 0);
    begin
        ind_tdata  <= (others => '0');
        ind_tvalid <= '0';

        wait until resetn = '1';
        wait for clk_period * 10;

        while not endfile(file_Indices) loop
            readline(file_Indices, v_line_I);
            hread(v_line_I, v_data_I);

            wait until falling_edge(clk);
            ind_tdata  <= v_data_I;
            ind_tvalid <= '1';

            -- Hold until handshake completes
            wait until rising_edge(clk) and ind_tready = '1';
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

                if C_tlast = '1' then
                    report "Simulation complete: C_tlast asserted" severity note;
                    std.env.finish;
                end if;
            end if;
        end if;
    end process;

    -----------------------------------------------------------
    -- Hard Timeout (safety net to prevent runaway simulation)
    -----------------------------------------------------------
    TIMEOUT : process
    begin
        wait for 500 us;
        report "Simulation timeout: C_tlast never asserted within 500 us" severity failure;
    end process;

end architecture wrapper_2to4_arch_TB;
