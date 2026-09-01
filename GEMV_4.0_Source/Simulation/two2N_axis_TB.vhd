library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;
use STD.textio.all;
use IEEE.std_logic_textio.all;

-- ----------------------------------------------------------------------------
-- STRESS co-simulation testbench for two2N_axis -- the AXIS splitter wrapper
-- around the 2:M sparse engine (INTEGRATION_PLAN.md 0.2b, STEPS S23).
--
-- This is two2N_TB.vhd with ONE change: the DUT is the wrapper instead of the
-- bare two2N, and the bundled stimulus buses are sliced into the wrapper's 17
-- per-channel AXIS ports in the port map. Stimulus generation, stress knobs,
-- drain detection and the monitor are byte-identical to two2N_TB, on purpose --
-- the whole value of this testbench is that ANY difference in output.txt is
-- attributable to the wrapper and nothing else.
--
-- Two things ONLY the wrapper can get wrong, so both are checked here:
--
--   1. INDEX TLAST. two2N has no index tlast port; the wrapper declares the
--      three inputs and leaves them unconnected. krnl_mm2s WILL drive them on
--      its last beat, so this TB drives them too (a conformant master), which is
--      how we prove the discard is harmless rather than merely assumed. If the
--      wrapper had accidentally routed one of them into the engine, the sparse
--      end-of-calculation marker would move and tlast.txt would change.
--
--   2. MASTER TKEEP. UG1393 requires all-1s on every beat (never all-zero).
--      Captured into c0..c3_keep and checked on every transfer; a violation is
--      a hard failure at the end of the run, not a note.
--
-- PASS = output.txt bit-identical to golden.txt AND tlast.txt carrying exactly
-- one '1', on the FINAL beat, with stress ON *and* OFF. Run
-- Emulation/compare_gemv4_py36.py unchanged (it checks both).
--
-- SERVER PATHS: adjust SPARSE_ROOT below if the sparse project does not sit at
-- /home/skoulas/GEMV_Sparse/GEMV_4.0_Source (confirmed 2026-08-25). It is the
-- only line that is environment-dependent.
-- ----------------------------------------------------------------------------

entity two2N_axis_TB is
end entity two2N_axis_TB;

architecture sim of two2N_axis_TB is

    constant CLK_PERIOD : time := 10 ns;

    -- ---- stress knobs (IDENTICAL to two2N_TB -- run this TB twice:           ----
    -- ----  FULL STRESS as below, then all three set to 0 / 0 / 0.0)           ----
    constant WGT_GAP_MAX  : integer := 4;      -- 0..N idle cycles before each weight/index beat
    constant ACT_GAP_MAX  : integer := 4;      -- 0..N idle cycles before each activation window
    constant TREADY_STALL : real    := 0.30;   -- fraction of cycles the output is back-pressured
    constant QUIET_CYCLES : integer := 2000;   -- no-new-output window that declares "done"

    -- Server (coroni) paths -- simulation runs there, not on the laptop.
    -- Windows equivalents, if ever needed locally:
    --   C:\Koulas\ECE\Thesis\Code\GEMV_4.0_Source\Emulation\<file>
    constant SPARSE_ROOT : string := "/home/skoulas/GEMV_Sparse/GEMV_4.0_Source/Emulation/";

    constant WPATH : string := SPARSE_ROOT & "weights.hex";
    constant IPATH : string := SPARSE_ROOT & "indices.hex";
    constant APATH : string := SPARSE_ROOT & "activations.hex";
    constant OPATH : string := SPARSE_ROOT & "output.txt";
    constant TPATH : string := SPARSE_ROOT & "tlast.txt";

    signal clk    : std_logic := '0';
    signal resetn : std_logic := '0';

    signal s_axis_w_tdata  : std_logic_vector(2047 downto 0) := (others => '0');
    signal s_axis_w_tvalid : std_logic_vector(7 downto 0)    := (others => '0');
    signal s_axis_w_tready : std_logic_vector(7 downto 0);
    signal s_axis_w_tlast  : std_logic_vector(7 downto 0)    := (others => '0');

    signal s_axis_ind_tdata  : std_logic_vector(767 downto 0) := (others => '0');
    signal s_axis_ind_tvalid : std_logic_vector(2 downto 0)   := (others => '0');
    signal s_axis_ind_tready : std_logic_vector(2 downto 0);
    signal s_axis_ind_tlast  : std_logic_vector(2 downto 0)   := (others => '0');

    signal s_axis_a_tdata  : std_logic_vector(511 downto 0) := (others => '0');
    signal s_axis_a_tvalid : std_logic_vector(1 downto 0)   := (others => '0');
    signal s_axis_a_tlast  : std_logic_vector(1 downto 0)   := (others => '0');
    signal s_axis_a_tready : std_logic_vector(1 downto 0);

    signal m_axis_c_tdata  : std_logic_vector(1023 downto 0);
    signal m_axis_c_tvalid : std_logic_vector(3 downto 0);
    signal m_axis_c_tlast  : std_logic_vector(3 downto 0);
    signal m_axis_c_tready : std_logic_vector(3 downto 0) := (others => '1');

    -- AXIS sidebands the bare two2N does not have
    constant KEEP_IN : std_logic_vector(31 downto 0) := (others => '1');
    signal   c0_keep, c1_keep, c2_keep, c3_keep : std_logic_vector(31 downto 0);

    signal beats_seen : integer := 0;
    signal stim_done  : boolean := false;
    signal keep_bad   : integer := 0;   -- counts master beats with tkeep /= all-1s

begin

    DUT : entity work.two2N_axis
        port map (
            ap_clk   => clk,
            ap_rst_n => resetn,

            -- weights: PC k = bits (256k+255 downto 256k)
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

            -- indices: PC k = bits (256k+255 downto 256k); Sparsity rides in
            -- ind2's padding -- bits [641:640] of the joined word, i.e.
            -- s_axis_ind2_tdata(129 downto 128). It is data, not a port.
            -- tlast IS driven here even though the engine has no such port.
            s_axis_ind0_tdata => s_axis_ind_tdata(  255 downto    0), s_axis_ind0_tkeep => KEEP_IN,
            s_axis_ind0_tvalid => s_axis_ind_tvalid(0), s_axis_ind0_tready => s_axis_ind_tready(0),
            s_axis_ind0_tlast  => s_axis_ind_tlast(0),

            s_axis_ind1_tdata => s_axis_ind_tdata(  511 downto  256), s_axis_ind1_tkeep => KEEP_IN,
            s_axis_ind1_tvalid => s_axis_ind_tvalid(1), s_axis_ind1_tready => s_axis_ind_tready(1),
            s_axis_ind1_tlast  => s_axis_ind_tlast(1),

            s_axis_ind2_tdata => s_axis_ind_tdata(  767 downto  512), s_axis_ind2_tkeep => KEEP_IN,
            s_axis_ind2_tvalid => s_axis_ind_tvalid(2), s_axis_ind2_tready => s_axis_ind_tready(2),
            s_axis_ind2_tlast  => s_axis_ind_tlast(2),

            -- activations
            s_axis_a0_tdata => s_axis_a_tdata(  255 downto    0), s_axis_a0_tkeep => KEEP_IN,
            s_axis_a0_tvalid => s_axis_a_tvalid(0), s_axis_a0_tready => s_axis_a_tready(0),
            s_axis_a0_tlast  => s_axis_a_tlast(0),

            s_axis_a1_tdata => s_axis_a_tdata(  511 downto  256), s_axis_a1_tkeep => KEEP_IN,
            s_axis_a1_tvalid => s_axis_a_tvalid(1), s_axis_a1_tready => s_axis_a_tready(1),
            s_axis_a1_tlast  => s_axis_a_tlast(1),

            -- outputs
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

    -- ---- output back-pressure: randomly deassert tready to stress prog_full ----
    TREADY_PROC : process
        variable s1 : positive := 137;
        variable s2 : positive := 597;
        variable r  : real;
    begin
        m_axis_c_tready <= (others => '1');
        wait until resetn = '1';
        loop
            wait until rising_edge(clk);
            uniform(s1, s2, r);
            if r < TREADY_STALL then
                m_axis_c_tready <= (others => '0');
            else
                m_axis_c_tready <= (others => '1');
            end if;
            exit when stim_done;
        end loop;
        m_axis_c_tready <= (others => '1');   -- release so any tail drains
        wait;
    end process TREADY_PROC;

    -- ---- stimulus with random input bubbles ----
    STIM : process
        file wfile : text open read_mode is WPATH;
        file ifile : text open read_mode is IPATH;
        file afile : text open read_mode is APATH;
        variable lw, li, la : line;
        variable wv : std_logic_vector(2047 downto 0);
        variable iv : std_logic_vector(767 downto 0);
        variable av : std_logic_vector(511 downto 0);
        variable s1 : positive := 24;
        variable s2 : positive := 9001;
        variable r  : real;
        variable gap  : integer;
        variable prev : integer := 0;
        variable quiet : integer := 0;
    begin
        resetn            <= '0';
        s_axis_w_tvalid   <= (others => '0');
        s_axis_w_tlast    <= (others => '0');
        s_axis_ind_tvalid <= (others => '0');
        s_axis_ind_tlast  <= (others => '0');
        s_axis_a_tvalid   <= (others => '0');
        s_axis_a_tlast    <= (others => '0');
        wait for 5*CLK_PERIOD;
        wait until rising_edge(clk);
        resetn <= '1';
        wait until rising_edge(clk);

        -- ---- activation windows (with random gaps, tlast on the last) ----
        while not endfile(afile) loop
            readline(afile, la);
            hread(la, av);
            uniform(s1, s2, r);
            gap := integer(r * real(ACT_GAP_MAX));
            for g in 1 to gap loop
                s_axis_a_tvalid <= (others => '0');
                s_axis_a_tlast  <= (others => '0');
                wait until rising_edge(clk);
            end loop;
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

        -- ---- weight+index beats (with random gaps, weight tlast on the last) ----
        -- The index tlast is driven on the last index beat exactly as krnl_mm2s
        -- will drive it. The wrapper must DISCARD it: if any of the three
        -- reached the engine, the end-of-calculation marker would land early and
        -- tlast.txt would show it.
        while not endfile(wfile) loop
            readline(wfile, lw);  hread(lw, wv);
            readline(ifile, li);  hread(li, iv);
            uniform(s1, s2, r);
            gap := integer(r * real(WGT_GAP_MAX));
            for g in 1 to gap loop
                s_axis_w_tvalid   <= (others => '0');
                s_axis_ind_tvalid <= (others => '0');
                wait until rising_edge(clk);
            end loop;
            s_axis_w_tdata    <= wv;
            s_axis_ind_tdata  <= iv;
            s_axis_w_tvalid   <= (others => '1');
            s_axis_ind_tvalid <= (others => '1');
            if endfile(wfile) then
                s_axis_w_tlast   <= (others => '1');
                s_axis_ind_tlast <= (others => '1');
            else
                s_axis_w_tlast   <= (others => '0');
                s_axis_ind_tlast <= (others => '0');
            end if;
            wait until rising_edge(clk)
                  and s_axis_w_tready   = "11111111"
                  and s_axis_ind_tready = "111";
        end loop;
        s_axis_w_tvalid   <= (others => '0');
        s_axis_ind_tvalid <= (others => '0');
        s_axis_w_tlast    <= (others => '0');
        s_axis_ind_tlast  <= (others => '0');

        -- ---- drain: wait until no new output beat for QUIET_CYCLES ----
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
        report "=== two2N_axis stress sim finished (" & integer'image(beats_seen) &
               " output beat(s) = " & integer'image(beats_seen*64) & " rows) ===" severity note;
        if keep_bad /= 0 then
            report "TKEEP VIOLATION on " & integer'image(keep_bad) &
                   " beat(s): a master drove tkeep /= all-1s" severity failure;
        else
            report "TKEEP ok on every output beat (all-1s)" severity note;
        end if;
        std.env.stop;
        wait;
    end process STIM;

    -- ---- monitor: dump each real output transfer (tvalid AND tready) ----
    -- Byte-identical to two2N_TB's monitor, plus the tkeep conformance check.
    -- output.txt : 64 data lanes per beat (checked against golden.txt)
    -- tlast.txt  : ONE line per beat recording that beat's TLAST -- the
    --              end-of-calculation marker. Only the FINAL beat may carry it.
    --              'X' = the 4 output PCs disagreed -> fork desync.
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
