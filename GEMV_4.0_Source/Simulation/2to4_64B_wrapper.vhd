library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;
use std.textio.all;
use IEEE.std_logic_textio.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
--
-- Description:
-- Testbench for the wrapper_2to4 (2to4_wrapper_64B) module.
-- Streams A matrix, B vector and indices from hex text files, captures the
-- C output stream to an output file and finishes on C_tlast.
-- Bus width is BUS_EL*EL_SIZE = 4*16 = 64 bits / 8 bytes per beat.
-- All three input channels use AXI-Stream handshaking (valid/ready).
-- ----------------------------------------------------------------------------

entity two2four_64B_wrapper_TB is
end entity two2four_64B_wrapper_TB;

architecture two2four_64B_wrapper_TB_arch of two2four_64B_wrapper_TB is

    -- NOTE: Generics are stripped after synthesis (resolved into the netlist),
    -- so the component declaration here matches the synthesized entity:
    -- no generics, only fixed-width ports that match the synthesis-time values.
    component wrapper_2to4 is
        port(
            clk         : in  std_logic;
            resetn      : in  std_logic;

            B_tdata     : in  std_logic_vector((4*16)-1 downto 0);
            B_tvalid    : in  std_logic;
            B_tlast     : in  std_logic;
            B_tready    : out std_logic;

            A_tdata     : in  std_logic_vector((4*16)-1 downto 0);
            A_tvalid    : in  std_logic;
            A_tready    : out std_logic;

            ind_tdata   : in  std_logic_vector(31 downto 0);
            ind_tvalid  : in  std_logic;
            ind_tready  : out std_logic;

            C_tdata     : out std_logic_vector(16-1 downto 0);
            C_tvalid    : out std_logic;
            C_tlast     : out std_logic;
            C_tready    : in  std_logic
        );
    end component wrapper_2to4;

    -- Configuration constants (must match the DUT generic map below)
    constant CLK_PERIOD : time    := 10 ns;
    constant EL_SIZE    : integer := 16;
    constant BUS_EL     : integer := 4;
    constant A_IDX      : integer := 2;
    constant B_IDX      : integer := 4;
    constant IND_NUM    : integer := 4;
    constant A_ROWS     : integer := 8;

    constant BUS_WIDTH  : integer := 4*16;
    constant IND_WIDTH  : integer := 32;

    -- DUT signals
    signal clk          : std_logic := '0';
    signal resetn       : std_logic := '0';

    -- B vector channel
    signal B_tdata      : std_logic_vector(BUS_WIDTH-1 downto 0) := (others => '0');
    signal B_tvalid     : std_logic := '0';
    signal B_tlast      : std_logic := '0';
    signal B_tready     : std_logic;

    -- A matrix channel
    signal A_tdata      : std_logic_vector(BUS_WIDTH-1 downto 0) := (others => '0');
    signal A_tvalid     : std_logic := '0';
    signal A_tready     : std_logic;

    -- Indices channel
    signal ind_tdata    : std_logic_vector(IND_WIDTH-1 downto 0) := (others => '0');
    signal ind_tvalid   : std_logic := '0';
    signal ind_tready   : std_logic;

    -- C output channel
    signal C_tdata      : std_logic_vector(16-1 downto 0);
    signal C_tvalid     : std_logic;
    signal C_tlast      : std_logic;
    signal C_tready     : std_logic := '1';

    -- Stimulus / capture files (one hex value per line)
    file file_A         : text open read_mode  is "A_8x4_HW.txt";
    file file_B         : text open read_mode  is "B_8x1_HW.txt";
    file file_Indices   : text open read_mode  is "Indices_8x2_HW.txt";
    file file_Output    : text open write_mode is "Output_2to4_64B.txt";

begin

    -- ------------------------------------------------------------------------
    -- Device Under Test
    -- ------------------------------------------------------------------------
    DUT : wrapper_2to4
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

    -- ------------------------------------------------------------------------
    -- Clock generation
    -- ------------------------------------------------------------------------
    CLK_GEN : process
    begin
        clk <= '1';
        wait for CLK_PERIOD/2;
        clk <= '0';
        wait for CLK_PERIOD/2;
    end process;

    -- ------------------------------------------------------------------------
    -- Reset
    -- ------------------------------------------------------------------------
    RESET_PROC : process
    begin
        resetn <= '0';
        wait for CLK_PERIOD*50;
        resetn <= '1';
        wait;
    end process;

    -----------------------------------------------------------
    -- Stream B vector beats from file_B (AXI-Stream handshake)
    -----------------------------------------------------------
    READ_B : process
        variable v_line_B : line;
        variable v_data_B : std_logic_vector(BUS_WIDTH-1 downto 0);
        variable is_last  : boolean;
    begin
        B_tdata  <= (others => '0');
        B_tvalid <= '0';
        B_tlast  <= '0';

        wait until resetn = '1';
        wait for CLK_PERIOD*10;

        while not endfile(file_B) loop
            wait until rising_edge(clk);
            readline(file_B, v_line_B);
            hread(v_line_B, v_data_B);
            is_last := endfile(file_B);

            B_tdata  <= v_data_B;
            B_tvalid <= '1';
            if endfile(file_B) then
                B_tlast <= '1';
            else
                B_tlast <= '0';
                wait until rising_edge(clk);
                B_tvalid <= '0';
                for i in 1 to 9 loop
                    wait until rising_edge(clk);
                end loop;
            end if;
        end loop;

        wait until rising_edge(clk);
        B_tvalid <= '0';
        B_tlast  <= '0';
        wait;
    end process;

    -----------------------------------------------------------
    -- Stream A matrix beats from file_A (AXI-Stream handshake)
    -----------------------------------------------------------
    READ_A : process
        variable v_line_A : line;
        variable v_data_A : std_logic_vector(BUS_WIDTH-1 downto 0);
    begin
        A_tdata  <= (others => '0');
        A_tvalid <= '0';

        wait until resetn = '1';
        wait for CLK_PERIOD*10;

        while not endfile(file_A) loop
            wait until rising_edge(clk);
            readline(file_A, v_line_A);
            hread(v_line_A, v_data_A);

            A_tdata  <= v_data_A;
            A_tvalid <= '1';

            wait until rising_edge(clk);
            A_tvalid <= '0';
            for i in 1 to 2 loop
                wait until rising_edge(clk);
            end loop;
        end loop;

        wait until rising_edge(clk);
        A_tvalid <= '0';
        wait;
    end process;

    -----------------------------------------------------------
    -- Stream indices beats from file_Indices (AXI-Stream handshake)
    -----------------------------------------------------------
    READ_INDICES : process
        variable v_line_I : line;
        variable v_data_I : std_logic_vector(IND_WIDTH-1 downto 0);
    begin
        ind_tdata  <= (others => '0');
        ind_tvalid <= '0';

        wait until resetn = '1';
        wait for CLK_PERIOD*10;

        while not endfile(file_Indices) loop
            wait until rising_edge(clk);
            readline(file_Indices, v_line_I);
            read(v_line_I, v_data_I);

            ind_tdata  <= v_data_I;
            ind_tvalid <= '1';

            wait until rising_edge(clk);
            ind_tvalid <= '0';
            for i in 1 to 4 loop
                wait until rising_edge(clk);
            end loop;
                
        end loop;

        wait until rising_edge(clk);
        ind_tvalid <= '0';
        wait;
    end process;

    -----------------------------------------------------------
    -- Capture C output to file; finish on C_tlast
    -- C_tready is held '1' (TB always ready to consume)
    -----------------------------------------------------------
    WRITE_OUTPUT : process(clk)
        variable v_line_out : line;
    begin
        if rising_edge(clk) then
            if C_tvalid = '1' and C_tready = '1' then
                hwrite(v_line_out, C_tdata);
                writeline(file_Output, v_line_out);

                if C_tlast = '1' then
                    report "Simulation complete: C_tlast asserted" severity note;
                    file_close(file_Output);
                    std.env.finish;
                end if;
            end if;
        end if;
    end process;

    -----------------------------------------------------------
    -- Hard timeout (safety net to prevent runaway simulation)
    -----------------------------------------------------------
    TIMEOUT : process
    begin
        wait for 500 us;
        report "Simulation timeout: C_tlast never asserted within 500 us" severity failure;
    end process;

end architecture two2four_64B_wrapper_TB_arch;
