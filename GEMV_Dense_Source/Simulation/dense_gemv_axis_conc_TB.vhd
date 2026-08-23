library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;
use STD.textio.all;
use IEEE.std_logic_textio.all;

-- ----------------------------------------------------------------------------
-- CONCURRENT-STIMULUS testbench for dense_gemv_axis.
--
-- WHY THIS EXISTS
-- dense_gemv_axis_TB drives the streams SEQUENTIALLY: every activation window
-- first, then every weight beat. The hardware does not. On the U280 the ten
-- krnl_mm2s compute units are launched together, so weights and activations
-- arrive INTERLEAVED, and the (short) activation stream finishes long before
-- the (long) weight stream does.
--
-- That difference is not cosmetic. On hardware, dense passes at nwin=2 and
-- fails at nwin=4/8/16/32 with the accumulator flushing after roughly the first
-- window or two instead of after the whole lap -- while the sequential
-- testbench passes bit-exact on the same stimulus. So the sequential TB simply
-- never reaches the state the board reaches.
--
-- WHAT CHANGED
-- The single STIM process is split into two independent processes that run
-- concurrently from the same start signal:
--     ACT_STIM : drives s_axis_a_* only
--     WGT_STIM : drives s_axis_w_* only
-- They touch disjoint signals, so there is no resolution conflict. A separate
-- CTRL process owns reset, and DRAIN waits for both streams plus a quiet period.
--
-- Everything else -- file paths, stress knobs, monitor, output format -- is
-- identical to dense_gemv_axis_TB, so results are directly comparable and
-- compare_dense_py36.py runs unchanged.
--
-- ACT_LEAD lets you sweep the arrival relationship: 0 = both streams start on
-- the same cycle (closest to hardware). Raising it delays activations, which is
-- useful for finding exactly which skew breaks the design.
-- ----------------------------------------------------------------------------

entity dense_gemv_axis_conc_TB is
end entity dense_gemv_axis_conc_TB;

architecture sim of dense_gemv_axis_conc_TB is

    constant CLK_PERIOD : time := 10 ns;

    -- ---- stress knobs (0 / 0 / 0.0 = back-to-back, like the movers) ----
    constant WGT_GAP_MAX  : integer := 0;
    constant ACT_GAP_MAX  : integer := 0;
    constant TREADY_STALL : real    := 0.0;
    constant QUIET_CYCLES : integer := 2000;

    -- Cycles to delay the activation stream relative to the weight stream.
    -- 0 = launched together, which is what the hardware does.
    constant ACT_LEAD     : integer := 0;

    constant WPATH : string := "/home/skoulas/GEMV_Dense/GEMV_Dense_Source/Emulation/weights.hex";
    constant APATH : string := "/home/skoulas/GEMV_Dense/GEMV_Dense_Source/Emulation/activations.hex";
    constant OPATH : string := "/home/skoulas/GEMV_Dense/GEMV_Dense_Source/Emulation/output.txt";
    constant TPATH : string := "/home/skoulas/GEMV_Dense/GEMV_Dense_Source/Emulation/tlast.txt";

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
    signal keep_bad   : integer := 0;

    signal go        : boolean := false;   -- reset released, streams may start
    signal act_done  : boolean := false;
    signal wgt_done  : boolean := false;
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

    -- ---- reset, then release both stimulus processes together ----
    CTRL : process
    begin
        resetn <= '0';
        go     <= false;
        wait for 5*CLK_PERIOD;
        wait until rising_edge(clk);
        resetn <= '1';
        wait until rising_edge(clk);
        go <= true;            -- both streams start from here
        wait;
    end process CTRL;

    -- ---- output back-pressure ----
    TREADY_PROC : process
        variable s1 : positive := 137;
        variable s2 : positive := 597;
        variable r  : real;
    begin
        m_axis_c_tready <= (others => '1');
        wait until resetn = '1';
        loop
            wait until rising_edge(clk);
            if TREADY_STALL > 0.0 then
                uniform(s1, s2, r);
                if r < TREADY_STALL then
                    m_axis_c_tready <= (others => '0');
                else
                    m_axis_c_tready <= (others => '1');
                end if;
            end if;
            exit when stim_done;
        end loop;
        m_axis_c_tready <= (others => '1');
        wait;
    end process TREADY_PROC;

    -- ---- ACTIVATIONS -- independent, concurrent with the weight stream ----
    -- On hardware this is mm2s_a0/a1: a short burst that completes early while
    -- the weight movers keep streaming.
    ACT_STIM : process
        file afile : text open read_mode is APATH;
        variable la : line;
        variable av : std_logic_vector(511 downto 0);
        variable s1 : positive := 24;
        variable s2 : positive := 9001;
        variable r  : real;
        variable gap : integer;
    begin
        s_axis_a_tvalid <= (others => '0');
        s_axis_a_tlast  <= (others => '0');
        wait until go;

        for d in 1 to ACT_LEAD loop
            wait until rising_edge(clk);
        end loop;

        while not endfile(afile) loop
            readline(afile, la);
            hread(la, av);
            if ACT_GAP_MAX > 0 then
                uniform(s1, s2, r);
                gap := integer(r * real(ACT_GAP_MAX));
                for g in 1 to gap loop
                    s_axis_a_tvalid <= (others => '0');
                    s_axis_a_tlast  <= (others => '0');
                    wait until rising_edge(clk);
                end loop;
            end if;
            s_axis_a_tdata  <= av;
            s_axis_a_tvalid <= (others => '1');
            if endfile(afile) then
                s_axis_a_tlast <= (others => '1');
            else
                s_axis_a_tlast <= (others => '0');
            end if;
            wait until rising_edge(clk) and s_axis_a_tready = "11";
        end loop;

        s_axis_a_tvalid <= (others => '0');
        s_axis_a_tlast  <= (others => '0');
        act_done <= true;      -- channel goes quiet, exactly as on hardware
        wait;
    end process ACT_STIM;

    -- ---- WEIGHTS -- independent, concurrent with the activation stream ----
    WGT_STIM : process
        file wfile : text open read_mode is WPATH;
        variable lw : line;
        variable wv : std_logic_vector(2047 downto 0);
        variable s1 : positive := 77;
        variable s2 : positive := 4242;
        variable r  : real;
        variable gap : integer;
    begin
        s_axis_w_tvalid <= (others => '0');
        s_axis_w_tlast  <= (others => '0');
        wait until go;

        while not endfile(wfile) loop
            readline(wfile, lw);
            hread(lw, wv);
            if WGT_GAP_MAX > 0 then
                uniform(s1, s2, r);
                gap := integer(r * real(WGT_GAP_MAX));
                for g in 1 to gap loop
                    s_axis_w_tvalid <= (others => '0');
                    wait until rising_edge(clk);
                end loop;
            end if;
            s_axis_w_tdata  <= wv;
            s_axis_w_tvalid <= (others => '1');
            if endfile(wfile) then
                s_axis_w_tlast <= (others => '1');
            else
                s_axis_w_tlast <= (others => '0');
            end if;
            wait until rising_edge(clk) and s_axis_w_tready = "11111111";
        end loop;

        s_axis_w_tvalid <= (others => '0');
        s_axis_w_tlast  <= (others => '0');
        wgt_done <= true;
        wait;
    end process WGT_STIM;

    -- ---- drain and stop ----
    DRAIN : process
        variable prev  : integer := 0;
        variable quiet : integer := 0;
    begin
        wait until act_done and wgt_done;
        prev  := beats_seen;
        quiet := 0;
        loop
            wait until rising_edge(clk);
            if beats_seen = prev then
                quiet := quiet + 1;
            else
                quiet := 0;
                prev  := beats_seen;
            end if;
            exit when quiet >= QUIET_CYCLES;
        end loop;

        stim_done <= true;
        report "=== concurrent sim finished (" & integer'image(beats_seen) &
               " output beat(s) = " & integer'image(beats_seen*64) & " rows), ACT_LEAD=" &
               integer'image(ACT_LEAD) severity note;
        if keep_bad /= 0 then
            report "TKEEP VIOLATION on " & integer'image(keep_bad) & " beat(s)" severity failure;
        end if;
        std.env.stop;
        wait;
    end process DRAIN;

    -- ---- monitor (identical to dense_gemv_axis_TB) ----
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
