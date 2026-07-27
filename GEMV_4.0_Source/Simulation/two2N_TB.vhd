library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;
use STD.textio.all;
use IEEE.std_logic_textio.all;

-- ----------------------------------------------------------------------------
-- STRESS co-simulation testbench for two2N (GEMV 4.0).
--
-- Reads the packed beats from Emulation/gemv4_cosim_gen.py and drives them, but
-- unlike the plain driver it INJECTS BUBBLES to emulate a bursty HBM read engine
-- and a back-pressuring HBM write path:
--   * random not-valid gaps on the weight/index and activation input streams
--     (deassert tvalid for a few cycles -> the ingress/weights FIFOs can underrun
--      -> exercises the DUT's ready_int_* stall paths),
--   * random back-pressure on the output (deassert m_axis_c_tready -> c_fifo fills
--      -> exercises prog_full / the global gate).
-- The DUT must stall correctly, so the dumped output.txt must still match
-- golden.txt bit-exact regardless of the bubbles.
--
-- Dumps every co-valid output beat (64 lanes, lane r = 8*core+block) to output.txt;
-- run Emulation/compare_gemv4.py. Absolute paths (XSim CWD is the sim run dir).
--
-- Tune the stress with the constants below. Completion is detected by a quiet
-- period (no new output beat for QUIET_CYCLES) so it adapts to any test size AND
-- terminates if the DUT hangs on a stall bug.
-- ----------------------------------------------------------------------------

entity two2N_TB is
end entity two2N_TB;

architecture sim of two2N_TB is

    constant CLK_PERIOD : time := 10 ns;

    -- ---- stress knobs ----
    constant WGT_GAP_MAX  : integer := 4;      -- 0..N idle cycles before each weight/index beat  (FULL STRESS)
    constant ACT_GAP_MAX  : integer := 4;      -- 0..N idle cycles before each activation window   (FULL STRESS)
    constant TREADY_STALL : real    := 0.30;   -- fraction of cycles the output is back-pressured   (FULL STRESS)
    constant QUIET_CYCLES : integer := 2000;   -- no-new-output window that declares "done"

    constant WPATH : string := "C:\Koulas\ECE\Thesis\Code\GEMV_4.0_Source\Emulation\weights.hex";
    constant IPATH : string := "C:\Koulas\ECE\Thesis\Code\GEMV_4.0_Source\Emulation\indices.hex";
    constant APATH : string := "C:\Koulas\ECE\Thesis\Code\GEMV_4.0_Source\Emulation\activations.hex";
    constant OPATH : string := "C:\Koulas\ECE\Thesis\Code\GEMV_4.0_Source\Emulation\output.txt";
    constant TPATH : string := "C:\Koulas\ECE\Thesis\Code\GEMV_4.0_Source\Emulation\tlast.txt";

    signal clk    : std_logic := '0';
    signal resetn : std_logic := '0';

    signal s_axis_w_tdata  : std_logic_vector(2047 downto 0) := (others => '0');
    signal s_axis_w_tvalid : std_logic_vector(7 downto 0)    := (others => '0');
    signal s_axis_w_tready : std_logic_vector(7 downto 0);
    signal s_axis_w_tlast  : std_logic_vector(7 downto 0)    := (others => '0');

    signal s_axis_ind_tdata  : std_logic_vector(767 downto 0) := (others => '0');
    signal s_axis_ind_tvalid : std_logic_vector(2 downto 0)   := (others => '0');
    signal s_axis_ind_tready : std_logic_vector(2 downto 0);

    signal s_axis_a_tdata  : std_logic_vector(511 downto 0) := (others => '0');
    signal s_axis_a_tvalid : std_logic_vector(1 downto 0)   := (others => '0');
    signal s_axis_a_tlast  : std_logic_vector(1 downto 0)   := (others => '0');
    signal s_axis_a_tready : std_logic_vector(1 downto 0);

    signal m_axis_c_tdata  : std_logic_vector(1023 downto 0);
    signal m_axis_c_tvalid : std_logic_vector(3 downto 0);
    signal m_axis_c_tlast  : std_logic_vector(3 downto 0);
    signal m_axis_c_tready : std_logic_vector(3 downto 0) := (others => '1');

    signal beats_seen : integer := 0;
    signal stim_done  : boolean := false;

begin

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
                s_axis_w_tlast <= (others => '1');
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
        report "=== stress sim finished (" & integer'image(beats_seen) &
               " output beat(s) = " & integer'image(beats_seen*64) & " rows) ===" severity note;
        std.env.stop;
        wait;
    end process STIM;

    -- ---- monitor: dump each real output transfer (tvalid AND tready) ----
    -- output.txt : 64 data lanes per beat (checked against golden.txt)
    -- tlast.txt  : ONE line per beat recording that beat's TLAST -- the
    --              end-of-calculation marker. Only the FINAL beat may carry it.
    --              Without this the data compare passes while the marker sits on
    --              the wrong beat (it is a sideband, invisible in output.txt).
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
            end if;
        end if;
    end process MON;

end architecture sim;
