library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use STD.textio.all;
use IEEE.std_logic_textio.all;

-- ----------------------------------------------------------------------------
-- Beat-level co-simulation testbench for two2N (top module, GEMV 4.0).
--
-- Reads the packed bus beats produced by Emulation/gemv4_cosim_gen.py and drives
-- them into the DUT, then dumps the co-valid output beat(s) to output.txt so
-- Emulation/compare_bfloat16.py can diff golden.txt vs output.txt.
--
-- Input files (hex, little-endian, one beat per line):
--   weights.hex     : NBEATS lines x 512 hex (2048b)  -- 128 bf16 / beat
--   indices.hex     : NBEATS lines x 192 hex ( 768b)  -- 640b idx + Sparsity[641:640]
--   activations.hex : NWIN   lines x 128 hex ( 512b)  -- 32 bf16 / window
-- Output file:
--   output.txt      : one bf16 (4 hex) per line, lane r = 8*core+block, r = 0..63
--
-- The read engine is emulated here: activation tlast is asserted on the last
-- activation window; weight tlast (end-of-matrix) on the last weight beat.
-- Beat writes are gated on the AXIS s_axis_*_tready handshake so exactly the
-- file contents are written (no duplicates).  Sparsity rides in each index beat.
--
-- Paths are absolute (XSim's CWD is the sim run dir); adjust if you relocate.
-- ----------------------------------------------------------------------------

entity two2N_TB is
end entity two2N_TB;

architecture sim of two2N_TB is

    constant CLK_PERIOD : time := 10 ns;

    constant WPATH : string := "C:\Koulas\ECE\Thesis\Code\GEMV_4.0_Source\Emulation\weights.hex";
    constant IPATH : string := "C:\Koulas\ECE\Thesis\Code\GEMV_4.0_Source\Emulation\indices.hex";
    constant APATH : string := "C:\Koulas\ECE\Thesis\Code\GEMV_4.0_Source\Emulation\activations.hex";
    constant OPATH : string := "C:\Koulas\ECE\Thesis\Code\GEMV_4.0_Source\Emulation\output.txt";

    -- clock / reset
    signal clk    : std_logic := '0';
    signal resetn : std_logic := '0';

    -- weights (8 PCs)
    signal s_axis_w_tdata  : std_logic_vector(2047 downto 0) := (others => '0');
    signal s_axis_w_tvalid : std_logic_vector(7 downto 0)    := (others => '0');
    signal s_axis_w_tready : std_logic_vector(7 downto 0);
    signal s_axis_w_tlast  : std_logic_vector(7 downto 0)    := (others => '0');

    -- indices (3 PCs)
    signal s_axis_ind_tdata  : std_logic_vector(767 downto 0) := (others => '0');
    signal s_axis_ind_tvalid : std_logic_vector(2 downto 0)   := (others => '0');
    signal s_axis_ind_tready : std_logic_vector(2 downto 0);

    -- activations (2 PCs)
    signal s_axis_a_tdata  : std_logic_vector(511 downto 0) := (others => '0');
    signal s_axis_a_tvalid : std_logic_vector(1 downto 0)   := (others => '0');
    signal s_axis_a_tlast  : std_logic_vector(1 downto 0)   := (others => '0');
    signal s_axis_a_tready : std_logic_vector(1 downto 0);

    -- output (4 PCs)
    signal m_axis_c_tdata  : std_logic_vector(1023 downto 0);
    signal m_axis_c_tvalid : std_logic_vector(3 downto 0);
    signal m_axis_c_tlast  : std_logic_vector(3 downto 0);
    signal m_axis_c_tready : std_logic_vector(3 downto 0) := (others => '1');

    signal stim_done  : boolean := false;
    signal beats_seen : integer := 0;

begin

    -- DUT (generic defaults -> the resolved widths above)
    DUT : entity work.two2N
        port map (
            clk               => clk,
            resetn            => resetn,

            s_axis_w_tdata    => s_axis_w_tdata,
            s_axis_w_tvalid   => s_axis_w_tvalid,
            s_axis_w_tready   => s_axis_w_tready,
            s_axis_w_tlast    => s_axis_w_tlast,

            s_axis_ind_tdata  => s_axis_ind_tdata,
            s_axis_ind_tvalid => s_axis_ind_tvalid,
            s_axis_ind_tready => s_axis_ind_tready,

            s_axis_a_tdata    => s_axis_a_tdata,
            s_axis_a_tvalid   => s_axis_a_tvalid,
            s_axis_a_tlast    => s_axis_a_tlast,
            s_axis_a_tready   => s_axis_a_tready,

            m_axis_c_tdata    => m_axis_c_tdata,
            m_axis_c_tvalid   => m_axis_c_tvalid,
            m_axis_c_tlast    => m_axis_c_tlast,
            m_axis_c_tready   => m_axis_c_tready
        );

    clk <= not clk after CLK_PERIOD/2;

    -- stimulus: stream the packed beats from the hex files
    STIM : process
        file wfile : text open read_mode is WPATH;
        file ifile : text open read_mode is IPATH;
        file afile : text open read_mode is APATH;
        variable lw, li, la : line;
        variable wv : std_logic_vector(2047 downto 0);
        variable iv : std_logic_vector(767 downto 0);
        variable av : std_logic_vector(511 downto 0);
    begin
        -- reset
        resetn            <= '0';
        s_axis_w_tvalid   <= (others => '0');
        s_axis_w_tlast    <= (others => '0');
        s_axis_ind_tvalid <= (others => '0');
        s_axis_a_tvalid   <= (others => '0');
        s_axis_a_tlast    <= (others => '0');
        wait for 5*CLK_PERIOD;
        wait until rising_edge(clk);
        resetn <= '1';
        wait until rising_edge(clk);

        -- ---- write every activation window (tlast on the last) ----
        while not endfile(afile) loop
            readline(afile, la);
            hread(la, av);
            s_axis_a_tdata  <= av;
            s_axis_a_tvalid <= (others => '1');
            if endfile(afile) then
                s_axis_a_tlast <= (others => '1');   -- end-of-vector
            else
                s_axis_a_tlast <= (others => '0');
            end if;
            wait until rising_edge(clk) and s_axis_a_tready = "11";
        end loop;
        s_axis_a_tvalid <= (others => '0');
        s_axis_a_tlast  <= (others => '0');

        -- ---- write every weight+index beat (weight tlast on the last) ----
        while not endfile(wfile) loop
            readline(wfile, lw);  hread(lw, wv);
            readline(ifile, li);  hread(li, iv);
            s_axis_w_tdata    <= wv;
            s_axis_ind_tdata  <= iv;
            s_axis_w_tvalid   <= (others => '1');
            s_axis_ind_tvalid <= (others => '1');
            if endfile(wfile) then
                s_axis_w_tlast <= (others => '1');   -- end of weight matrix
            else
                s_axis_w_tlast <= (others => '0');
            end if;
            wait until rising_edge(clk)
                  and s_axis_w_tready   = "11111111"
                  and s_axis_ind_tready = "111";
        end loop;
        s_axis_w_tvalid   <= (others => '0');
        s_axis_ind_tvalid <= (others => '0');
        s_axis_w_tlast    <= (others => '0');

        -- let the datapath drain (freeze walk + mul->add->accum->c_fifo)
        wait for 400*CLK_PERIOD;
        stim_done <= true;

        if beats_seen = 0 then
            report "NO OUTPUT beat was produced within the timeout." severity error;
        end if;
        report "=== simulation finished (" & integer'image(beats_seen) &
               " output beat(s)) ===" severity note;
        std.env.stop;
        wait;
    end process;

    -- monitor: dump each co-valid output beat to output.txt (64 lanes/beat)
    MON : process(clk)
        file outfile : text open write_mode is OPATH;
        variable ol : line;
    begin
        if rising_edge(clk) then
            if (m_axis_c_tvalid AND m_axis_c_tready) /= "0000" then   -- real AXIS transfer
                beats_seen <= beats_seen + 1;
                report "OUTPUT beat " & integer'image(beats_seen) &
                       "  tlast=" & integer'image(to_integer(unsigned(m_axis_c_tlast)))
                       severity note;
                for r in 0 to 63 loop            -- lane r = 8*core + block
                    hwrite(ol, m_axis_c_tdata(16*r+15 downto 16*r));
                    writeline(outfile, ol);
                end loop;
            end if;
        end if;
    end process;

end architecture sim;
