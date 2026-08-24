library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;
use STD.textio.all;
use IEEE.std_logic_textio.all;

-- ----------------------------------------------------------------------------
-- TWO-CALCULATION testbench for dense_gemv_axis -- verifies the end-of-calculation
-- teardown (the fix for the single-shot bug found on hardware 2026-08-23).
--
-- WHY THIS EXISTS
-- Every other testbench runs ONE calculation from reset, so none of them can see
-- the bug: the replay FIFO kept the previous activation vector, and the next load
-- appended behind it, so calculation 2 used calculation 1's activations.
--
-- On hardware, two separate ./host invocations with no xbutil reset in between
-- ARE two back-to-back calculations -- the FPGA stays programmed and never sees
-- ap_rst_n again. This testbench models exactly that: two complete calculations,
-- DIFFERENT data, ONE continuous simulation, NO reset in between.
--
-- STIMULUS -- two independent sets, generated with different seeds:
--     weights_a.hex / activations_a.hex / golden_a.txt      (e.g. --seed 111)
--     weights_b.hex / activations_b.hex / golden_b.txt      (e.g. --seed 222)
--   cat golden_a.txt golden_b.txt > golden.txt
-- then compare_dense_py36.py checks both halves in one go.
--
-- The B data MUST differ from A. With identical data a broken teardown still
-- produces the right answer -- the stale vector happens to be the correct one.
-- That is exactly how the bug hid on hardware for several runs.
--
-- PASS: both halves bit-exact, and TLAST seen exactly TWICE (once per
-- calculation, on its final beat).
--
-- GAP_CYCLES is the idle time between the two calculations. The teardown needs
-- the drain plus an 8-cycle FIFO flush; 64 is comfortable. Lower it to probe how
-- quickly a new calculation may legally start.
-- ----------------------------------------------------------------------------

entity dense_gemv_axis_rerun_TB is
end entity dense_gemv_axis_rerun_TB;

architecture sim of dense_gemv_axis_rerun_TB is

    constant CLK_PERIOD  : time    := 10 ns;
    constant GAP_CYCLES  : integer := 64;
    constant QUIET_CYCLES: integer := 2000;
    constant WATCHDOG    : integer := 4000000;   -- hard stop if the design hangs

    constant DIR    : string := "/home/skoulas/GEMV_Dense/GEMV_Dense_Source/Emulation/";
    constant WPATH_A : string := DIR & "weights_a.hex";
    constant APATH_A : string := DIR & "activations_a.hex";
    constant WPATH_B : string := DIR & "weights_b.hex";
    constant APATH_B : string := DIR & "activations_b.hex";
    constant OPATH   : string := DIR & "output.txt";
    constant TPATH   : string := DIR & "tlast.txt";

    signal clk    : std_logic := '0';
    signal resetn : std_logic := '0';

    signal s_axis_w_tdata  : std_logic_vector(2047 downto 0) := (others => '0');
    signal s_axis_w_tvalid : std_logic_vector(7 downto 0)    := (others => '0');
    signal s_axis_w_tready : std_logic_vector(7 downto 0);
    signal s_axis_w_tlast  : std_logic_vector(7 downto 0)    := (others => '0');

    signal s_axis_a_tdata  : std_logic_vector(511 downto 0) := (others => '0');
    signal s_axis_a_tvalid : std_logic_vector(1 downto 0)   := (others => '0');
    signal s_axis_a_tlast  : std_logic_vector(1 downto 0)   := (others => '0');
    signal s_axis_a_tready : std_logic_vector(1 downto 0);

    signal m_axis_c_tdata  : std_logic_vector(1023 downto 0);
    signal m_axis_c_tvalid : std_logic_vector(3 downto 0);
    signal m_axis_c_tlast  : std_logic_vector(3 downto 0);
    signal m_axis_c_tready : std_logic_vector(3 downto 0) := (others => '1');

    constant KEEP_IN : std_logic_vector(31 downto 0) := (others => '1');
    signal   c0_keep, c1_keep, c2_keep, c3_keep : std_logic_vector(31 downto 0);

    signal beats_seen : integer := 0;
    signal ends_seen  : integer := 0;      -- calculations completed (TLAST beats)
    signal beats_at_a : integer := -1;     -- beat count when calculation A ended
    signal keep_bad   : integer := 0;

    signal go        : boolean := false;
    signal run_b_go  : boolean := false;
    signal a_done_b  : boolean := false;   -- activation stream finished set B
    signal w_done_b  : boolean := false;   -- weight stream finished set B
    signal stim_done : boolean := false;

begin

    DUT : entity work.dense_gemv_axis
        port map (
            ap_clk   => clk,
            ap_rst_n => resetn,

            s_axis_w0_tdata => s_axis_w_tdata(  255 downto    0), s_axis_w0_tkeep => KEEP_IN,
            s_axis_w0_tvalid => s_axis_w_tvalid(0), s_axis_w0_tready => s_axis_w_tready(0),
            s_axis_w0_tlast  => s_axis_w_tlast(0),

            s_axis_w1_tdata => s_axis_w_tdata(  511 downto  256), s_axis_w1_tkeep => KEEP_IN,
            s_axis_w1_tvalid => s_axis_w_tvalid(1), s_axis_w1_tready => s_axis_w_tready(1),
            s_axis_w1_tlast  => s_axis_w_tlast(1),

            s_axis_w2_tdata => s_axis_w_tdata(  767 downto  512), s_axis_w2_tkeep => KEEP_IN,
            s_axis_w2_tvalid => s_axis_w_tvalid(2), s_axis_w2_tready => s_axis_w_tready(2),
            s_axis_w2_tlast  => s_axis_w_tlast(2),

            s_axis_w3_tdata => s_axis_w_tdata( 1023 downto  768), s_axis_w3_tkeep => KEEP_IN,
            s_axis_w3_tvalid => s_axis_w_tvalid(3), s_axis_w3_tready => s_axis_w_tready(3),
            s_axis_w3_tlast  => s_axis_w_tlast(3),

            s_axis_w4_tdata => s_axis_w_tdata( 1279 downto 1024), s_axis_w4_tkeep => KEEP_IN,
            s_axis_w4_tvalid => s_axis_w_tvalid(4), s_axis_w4_tready => s_axis_w_tready(4),
            s_axis_w4_tlast  => s_axis_w_tlast(4),

            s_axis_w5_tdata => s_axis_w_tdata( 1535 downto 1280), s_axis_w5_tkeep => KEEP_IN,
            s_axis_w5_tvalid => s_axis_w_tvalid(5), s_axis_w5_tready => s_axis_w_tready(5),
            s_axis_w5_tlast  => s_axis_w_tlast(5),

            s_axis_w6_tdata => s_axis_w_tdata( 1791 downto 1536), s_axis_w6_tkeep => KEEP_IN,
            s_axis_w6_tvalid => s_axis_w_tvalid(6), s_axis_w6_tready => s_axis_w_tready(6),
            s_axis_w6_tlast  => s_axis_w_tlast(6),

            s_axis_w7_tdata => s_axis_w_tdata( 2047 downto 1792), s_axis_w7_tkeep => KEEP_IN,
            s_axis_w7_tvalid => s_axis_w_tvalid(7), s_axis_w7_tready => s_axis_w_tready(7),
            s_axis_w7_tlast  => s_axis_w_tlast(7),

            s_axis_a0_tdata => s_axis_a_tdata(  255 downto    0), s_axis_a0_tkeep => KEEP_IN,
            s_axis_a0_tvalid => s_axis_a_tvalid(0), s_axis_a0_tready => s_axis_a_tready(0),
            s_axis_a0_tlast  => s_axis_a_tlast(0),

            s_axis_a1_tdata => s_axis_a_tdata(  511 downto  256), s_axis_a1_tkeep => KEEP_IN,
            s_axis_a1_tvalid => s_axis_a_tvalid(1), s_axis_a1_tready => s_axis_a_tready(1),
            s_axis_a1_tlast  => s_axis_a_tlast(1),

            m_axis_c0_tdata => m_axis_c_tdata(  255 downto    0), m_axis_c0_tkeep => c0_keep,
            m_axis_c0_tvalid => m_axis_c_tvalid(0), m_axis_c0_tready => m_axis_c_tready(0),
            m_axis_c0_tlast  => m_axis_c_tlast(0),

            m_axis_c1_tdata => m_axis_c_tdata(  511 downto  256), m_axis_c1_tkeep => c1_keep,
            m_axis_c1_tvalid => m_axis_c_tvalid(1), m_axis_c1_tready => m_axis_c_tready(1),
            m_axis_c1_tlast  => m_axis_c_tlast(1),

            m_axis_c2_tdata => m_axis_c_tdata(  767 downto  512), m_axis_c2_tkeep => c2_keep,
            m_axis_c2_tvalid => m_axis_c_tvalid(2), m_axis_c2_tready => m_axis_c_tready(2),
            m_axis_c2_tlast  => m_axis_c_tlast(2),

            m_axis_c3_tdata => m_axis_c_tdata( 1023 downto  768), m_axis_c3_tkeep => c3_keep,
            m_axis_c3_tvalid => m_axis_c_tvalid(3), m_axis_c3_tready => m_axis_c_tready(3),
            m_axis_c3_tlast  => m_axis_c_tlast(3)
        );

    clk <= not clk after CLK_PERIOD/2;

    -- ---- reset once, at the very start. NEVER again. ----
    CTRL : process
    begin
        resetn <= '0';
        wait for 5*CLK_PERIOD;
        wait until rising_edge(clk);
        resetn <= '1';
        wait until rising_edge(clk);
        go <= true;
        wait;
    end process CTRL;

    -- ---- start calculation B once A's end-of-calc TLAST has drained ----
    SEQ : process
    begin
        wait until ends_seen = 1;
        report "=== calculation A complete (" & integer'image(beats_seen) &
               " beat(s)); waiting " & integer'image(GAP_CYCLES) &
               " cycles, NO reset, then starting B ===" severity note;
        for i in 1 to GAP_CYCLES loop
            wait until rising_edge(clk);
        end loop;
        run_b_go <= true;
        wait;
    end process SEQ;

    -- ---- ACTIVATIONS: set A, then set B ----
    ACT_STIM : process
        file afile_a : text open read_mode is APATH_A;
        file afile_b : text open read_mode is APATH_B;
        variable la : line;
        variable av : std_logic_vector(511 downto 0);
    begin
        s_axis_a_tvalid <= (others => '0');
        s_axis_a_tlast  <= (others => '0');
        wait until go;

        while not endfile(afile_a) loop
            readline(afile_a, la); hread(la, av);
            s_axis_a_tdata  <= av;
            s_axis_a_tvalid <= (others => '1');
            if endfile(afile_a) then s_axis_a_tlast <= (others => '1');
            else                     s_axis_a_tlast <= (others => '0'); end if;
            wait until rising_edge(clk) and s_axis_a_tready = "11";
        end loop;
        s_axis_a_tvalid <= (others => '0');
        s_axis_a_tlast  <= (others => '0');

        wait until run_b_go;

        while not endfile(afile_b) loop
            readline(afile_b, la); hread(la, av);
            s_axis_a_tdata  <= av;
            s_axis_a_tvalid <= (others => '1');
            if endfile(afile_b) then s_axis_a_tlast <= (others => '1');
            else                     s_axis_a_tlast <= (others => '0'); end if;
            wait until rising_edge(clk) and s_axis_a_tready = "11";
        end loop;
        s_axis_a_tvalid <= (others => '0');
        s_axis_a_tlast  <= (others => '0');
        a_done_b <= true;
        wait;
    end process ACT_STIM;

    -- ---- WEIGHTS: set A, then set B ----
    WGT_STIM : process
        file wfile_a : text open read_mode is WPATH_A;
        file wfile_b : text open read_mode is WPATH_B;
        variable lw : line;
        variable wv : std_logic_vector(2047 downto 0);
    begin
        s_axis_w_tvalid <= (others => '0');
        s_axis_w_tlast  <= (others => '0');
        wait until go;

        while not endfile(wfile_a) loop
            readline(wfile_a, lw); hread(lw, wv);
            s_axis_w_tdata  <= wv;
            s_axis_w_tvalid <= (others => '1');
            if endfile(wfile_a) then s_axis_w_tlast <= (others => '1');
            else                     s_axis_w_tlast <= (others => '0'); end if;
            wait until rising_edge(clk) and s_axis_w_tready = "11111111";
        end loop;
        s_axis_w_tvalid <= (others => '0');
        s_axis_w_tlast  <= (others => '0');

        wait until run_b_go;

        while not endfile(wfile_b) loop
            readline(wfile_b, lw); hread(lw, wv);
            s_axis_w_tdata  <= wv;
            s_axis_w_tvalid <= (others => '1');
            if endfile(wfile_b) then s_axis_w_tlast <= (others => '1');
            else                     s_axis_w_tlast <= (others => '0'); end if;
            wait until rising_edge(clk) and s_axis_w_tready = "11111111";
        end loop;
        s_axis_w_tvalid <= (others => '0');
        s_axis_w_tlast  <= (others => '0');
        w_done_b <= true;
        wait;
    end process WGT_STIM;

    -- ---- drain, then verdict ----
    DRAIN : process
        variable prev  : integer := 0;
        variable quiet : integer := 0;
    begin
        wait until a_done_b and w_done_b;
        prev := beats_seen; quiet := 0;
        loop
            wait until rising_edge(clk);
            if beats_seen = prev then quiet := quiet + 1;
            else                      quiet := 0; prev := beats_seen; end if;
            exit when quiet >= QUIET_CYCLES;
        end loop;

        stim_done <= true;
        report "=== rerun sim finished: " & integer'image(beats_seen) &
               " total beat(s), calculation A ended at beat " &
               integer'image(beats_at_a) & ", TLAST seen " &
               integer'image(ends_seen) & " time(s) ===" severity note;

        if ends_seen /= 2 then
            report "FAIL: expected exactly 2 end-of-calculation TLAST beats, got " &
                   integer'image(ends_seen) severity failure;
        end if;
        if keep_bad /= 0 then
            report "TKEEP VIOLATION on " & integer'image(keep_bad) & " beat(s)" severity failure;
        end if;
        report "TLAST count OK (one per calculation). Now compare output.txt against "
             & "golden_a.txt ++ golden_b.txt." severity note;
        std.env.stop;
        wait;
    end process DRAIN;

    -- ---- watchdog: a broken teardown can deadlock rather than miscompute ----
    DOG : process
        variable n : integer := 0;
    begin
        loop
            wait until rising_edge(clk);
            n := n + 1;
            exit when stim_done or n >= WATCHDOG;
        end loop;
        if not stim_done then
            report "WATCHDOG: no completion after " & integer'image(WATCHDOG) &
                   " cycles -- beats=" & integer'image(beats_seen) &
                   " ends=" & integer'image(ends_seen) &
                   ". The second calculation never finished." severity failure;
        end if;
        wait;
    end process DOG;

    -- ---- monitor ----
    MON : process(clk)
        file outfile : text open write_mode is OPATH;
        file tlfile  : text open write_mode is TPATH;
        variable ol : line;
        variable tl : line;
    begin
        if rising_edge(clk) then
            if (m_axis_c_tvalid AND m_axis_c_tready) /= "0000" then
                beats_seen <= beats_seen + 1;
                for r in 0 to 63 loop
                    hwrite(ol, m_axis_c_tdata(16*r+15 downto 16*r));
                    writeline(outfile, ol);
                end loop;

                if m_axis_c_tlast = "1111" then
                    write(tl, string'("1"));
                    ends_seen <= ends_seen + 1;
                    if ends_seen = 0 then
                        beats_at_a <= beats_seen + 1;
                    end if;
                elsif m_axis_c_tlast = "0000" then
                    write(tl, string'("0"));
                else
                    write(tl, string'("X"));
                end if;
                writeline(tlfile, tl);

                if (c0_keep AND c1_keep AND c2_keep AND c3_keep) /= X"FFFFFFFF" then
                    keep_bad <= keep_bad + 1;
                end if;
            end if;
        end if;
    end process MON;

end architecture sim;
