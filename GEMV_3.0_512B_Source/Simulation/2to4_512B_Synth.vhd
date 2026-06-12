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
-- Testbench for the two2four (2to4_3.0_64B) module.
-- Streams A matrix, B vector and indices from hex text files, captures the
-- Cout stream to an output file and finishes on Ctlast.
-- Bus width is BUS_EL*EL_SIZE = 32*16 = 512 bits (64 bytes per beat).
-- ----------------------------------------------------------------------------

entity two2four_TB is
end entity two2four_TB;

architecture two2four_TB2_arch of two2four_TB is

    component two2four is
        -- generic(
        --     EL_SIZE     : integer := 16;
        --     BUS_EL      : integer := 32;
        --     A_IDX       : integer := 2;
        --     B_IDX       : integer := 4;
        --     IND_NUM     : integer := 4;
        --     A_ROWS      : integer := 64
        -- );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;

            B_in        : in std_logic_vector ((32*16)-1 downto 0);
            B_valid_in  : in std_logic;
            tlast_in    : in std_logic;

            A_in        : in std_logic_vector ((32*16)-1 downto 0);
            A_valid     : in std_logic;

            indices     : in std_logic_vector ((32*16)-1 downto 0);
            ind_valid   : in std_logic;

            Cout        : out std_logic_vector (16-1 downto 0);
            Cvalid      : out std_logic;
            Ctlast      : out std_logic
        );
    end component two2four;

    -- Configuration constants (must match the DUT generic map below)
    constant CLK_PERIOD : time    := 10 ns;
    constant EL_SIZE    : integer := 16;
    constant BUS_EL     : integer := 32;
    constant A_IDX      : integer := 2;
    constant B_IDX      : integer := 4;
    constant IND_NUM    : integer := 4;
    constant A_ROWS     : integer := 64;

    constant BUS_WIDTH  : integer := BUS_EL*EL_SIZE;   -- 512 bits / 64 bytes
    constant IND_WIDTH  : integer := BUS_EL*EL_SIZE;   -- 512 bits / 64 bytes

    -- DUT signals
    signal clk          : std_logic := '0';
    signal resetn       : std_logic := '0';

    signal B_in         : std_logic_vector(BUS_WIDTH-1 downto 0) := (others => '0');
    signal B_valid_in   : std_logic := '0';
    signal tlast_in     : std_logic := '0';

    signal A_in         : std_logic_vector(BUS_WIDTH-1 downto 0) := (others => '0');
    signal A_valid      : std_logic := '0';

    signal indices      : std_logic_vector(IND_WIDTH-1 downto 0) := (others => '0');
    signal ind_valid    : std_logic := '0';

    signal Cout         : std_logic_vector(EL_SIZE-1 downto 0);
    signal Cvalid       : std_logic;
    signal Ctlast       : std_logic;

    -- Stimulus / capture files (one hex value per line)
    file file_A         : text open read_mode  is "A_64x32_HW.txt";
    file file_B         : text open read_mode  is "B_64x1_HW.txt";
    file file_Indices   : text open read_mode  is "Indices_64x16_HW.txt";
    file file_Output    : text open write_mode is "Output_2to4_512B.txt";

begin

    -- ------------------------------------------------------------------------
    -- Device Under Test
    -- ------------------------------------------------------------------------
    DUT : two2four
--        generic map (
--            EL_SIZE => EL_SIZE,
--            BUS_EL  => BUS_EL,
--            A_IDX   => A_IDX,
--            B_IDX   => B_IDX,
--            IND_NUM => IND_NUM,
--            A_ROWS  => A_ROWS
--        )
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
        wait for CLK_PERIOD*40;
        resetn <= '1';
        wait;
    end process;

    -----------------------------------------------------------
    -- Stream B vector beats from file_B
    -----------------------------------------------------------
    READ_B : process
        variable v_line_B : line;
        variable v_data_B : std_logic_vector(BUS_WIDTH-1 downto 0);
    begin
        B_in       <= (others => '0');
        B_valid_in <= '0';
        tlast_in   <= '0';

        wait until resetn = '1';
        wait for CLK_PERIOD*10;

        while not endfile(file_B) loop
            wait until rising_edge(clk);
            readline(file_B, v_line_B);
            hread(v_line_B, v_data_B);

            B_in       <= v_data_B;
            B_valid_in <= '1';
            if endfile(file_B) then
                tlast_in <= '1';
            else
                tlast_in <= '0';
                wait until rising_edge(clk);
                B_valid_in <= '0';
                for i in 1 to 9 loop
                    wait until rising_edge(clk);
                end loop;
            end if;


        end loop;
        
        wait until rising_edge(clk);
        B_valid_in <= '0';
        tlast_in   <= '0';
        wait;
    end process;

    -----------------------------------------------------------
    -- Stream A matrix beats from file_A
    -----------------------------------------------------------
    READ_A : process
        variable v_line_A : line;
        variable v_data_A : std_logic_vector(BUS_WIDTH-1 downto 0);
    begin
        A_in    <= (others => '0');
        A_valid <= '0';

        wait until resetn = '1';
        wait for CLK_PERIOD*10;

        while not endfile(file_A) loop
            wait until rising_edge(clk);
            readline(file_A, v_line_A);
            hread(v_line_A, v_data_A);

            A_in    <= v_data_A;
            A_valid <= '1';

            wait until rising_edge(clk);
            A_valid <= '0';
            for i in 1 to 2 loop
                wait until rising_edge(clk);
            end loop;
        end loop;

        wait until rising_edge(clk);
        A_valid <= '0';
        wait;
    end process;

    -----------------------------------------------------------
    -- Stream indices beats from file_Indices
    -----------------------------------------------------------
    READ_INDICES : process
        variable v_line_I : line;
        variable v_data_I : std_logic_vector(IND_WIDTH-1 downto 0);
    begin
        indices   <= (others => '0');
        ind_valid <= '0';

        wait until resetn = '1';
        wait for CLK_PERIOD*10;

        while not endfile(file_Indices) loop
            wait until rising_edge(clk);
            readline(file_Indices, v_line_I);
            read(v_line_I, v_data_I);

            indices   <= v_data_I;
            ind_valid <= '1';

            wait until rising_edge(clk);
            ind_valid <= '0';
            for i in 1 to 4 loop
                wait until rising_edge(clk);
            end loop;

        end loop;

        wait until rising_edge(clk);
        ind_valid <= '0';
        wait;
    end process;

    -----------------------------------------------------------
    -- Capture Cout to output file; finish on Ctlast
    -----------------------------------------------------------
    WRITE_OUTPUT : process(clk)
        variable v_line_out : line;
    begin
        if rising_edge(clk) then
            if Cvalid = '1' then
                hwrite(v_line_out, Cout);
                writeline(file_Output, v_line_out);

                if Ctlast = '1' then
                    report "Simulation complete: Ctlast asserted" severity note;
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
        report "Simulation timeout: Ctlast never asserted within 500 us" severity failure;
    end process;

end architecture two2four_TB2_arch;
