library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Testbench for c_fifo (GEMV 4.0 output FORK, AXIS Data FIFOs)
--
-- Verifies the module's three behaviours:
--   1. Fork routing  : a co-valid 1024-bit beat splits into 4 PC streams, each
--                       PC getting its own 256-bit slice (slice p of beat n is
--                       tagged p*256+n, so each PC stream is self-checkable).
--   2. Independent drain : stalling one PC's tready holds only that PC; the
--                       others keep draining, and the stalled PC resumes in order.
--   3. prog_full     : a no-drain burst fills the FIFOs and asserts prog_full.
--
-- Self-checking: a per-PC counter checks that PC p emits beats 1,2,3,... in
-- order with the correct tag. Observation for tlast / prog_full edges.
-- Requires the axis_data_fifo_c IP (256-bit TDATA + TLAST + prog_full).
-- ----------------------------------------------------------------------------

entity c_fifo_4_0_TB is
end entity c_fifo_4_0_TB;

architecture sim of c_fifo_4_0_TB is

    constant CLK_PERIOD : time := 10 ns;
    constant BURST      : integer := 245;   -- > prog_full threshold (~240), < depth (256)

    -- DUT signals (PC_WIDTH=256, OUT_PCS=4)
    signal clk     : std_logic := '0';
    signal resetn  : std_logic := '0';

    signal Cout      : std_logic_vector(1023 downto 0) := (others => '0');
    signal Cvalid    : std_logic := '0';
    signal Ctlast    : std_logic := '0';
    signal prog_full : std_logic;

    signal m_axis_c_tdata  : std_logic_vector(1023 downto 0);
    signal m_axis_c_tvalid : std_logic_vector(3 downto 0);
    signal m_axis_c_tlast  : std_logic_vector(3 downto 0);
    signal m_axis_c_tready : std_logic_vector(3 downto 0) := (others => '0');

    -- 256-bit slice = sixteen copies of a 16-bit tag
    function tag256(constant v : integer) return std_logic_vector is
        variable r  : std_logic_vector(255 downto 0);
        variable vv : std_logic_vector(15 downto 0);
    begin
        vv := std_logic_vector(to_unsigned(v, 16));
        for i in 0 to 15 loop
            r(16*i+15 downto 16*i) := vv;
        end loop;
        return r;
    end function;

    function to_hex(constant v : std_logic_vector) return string is
        constant N   : integer := (v'length + 3) / 4;
        variable u   : unsigned(N*4-1 downto 0) := (others => '0');
        variable r   : string(1 to N);
        variable nib : integer;
    begin
        u(v'length-1 downto 0) := unsigned(v);
        for i in 0 to N-1 loop
            nib := to_integer(u((N-1-i)*4+3 downto (N-1-i)*4));
            if nib < 10 then
                r(i+1) := character'val(character'pos('0') + nib);
            else
                r(i+1) := character'val(character'pos('A') + nib - 10);
            end if;
        end loop;
        return r;
    end function;

    -- procedure builds the 1024-bit beat n: slice p = tag(p*256 + n)
    procedure drive_beat(signal c : out std_logic_vector(1023 downto 0);
                         constant n : integer) is
    begin
        for p in 0 to 3 loop
            c((p+1)*256-1 downto p*256) <= tag256(p*256 + n);
        end loop;
    end procedure;

begin

    DUT : entity work.c_fifo
        port map (
            clk             => clk,
            resetn          => resetn,
            Cout            => Cout,
            Cvalid          => Cvalid,
            Ctlast          => Ctlast,
            prog_full       => prog_full,
            m_axis_c_tdata  => m_axis_c_tdata,
            m_axis_c_tvalid => m_axis_c_tvalid,
            m_axis_c_tlast  => m_axis_c_tlast,
            m_axis_c_tready => m_axis_c_tready
        );

    clk <= not clk after CLK_PERIOD/2;

    STIM : process
    begin
        -- reset
        resetn          <= '0';
        Cvalid          <= '0';
        Ctlast          <= '0';
        m_axis_c_tready <= "0000";
        wait for 5*CLK_PERIOD;
        wait until rising_edge(clk);
        resetn <= '1';
        wait until rising_edge(clk);

        ------------------------------------------------------------------
        -- PHASE A : free drain. Write beats 1..3, all PCs ready.
        ------------------------------------------------------------------
        m_axis_c_tready <= "1111";
        for n in 1 to 3 loop
            drive_beat(Cout, n);
            Cvalid <= '1';
            wait until rising_edge(clk);
        end loop;
        Cvalid <= '0';
        wait for 8*CLK_PERIOD;            -- let all 4 PCs drain

        ------------------------------------------------------------------
        -- PHASE B : independent drain. Stall PC0 while writing 4..6;
        --           PC1..3 drain, PC0 holds, then PC0 resumes in order.
        ------------------------------------------------------------------
        m_axis_c_tready <= "1110";        -- PC0 not ready
        for n in 4 to 6 loop
            drive_beat(Cout, n);
            Cvalid <= '1';
            wait until rising_edge(clk);
        end loop;
        Cvalid <= '0';
        wait for 6*CLK_PERIOD;            -- PC1..3 drained; PC0 holding 4,5,6
        m_axis_c_tready <= "1111";        -- release PC0
        wait for 8*CLK_PERIOD;

        ------------------------------------------------------------------
        -- PHASE C : prog_full. No drain, write a burst that exceeds the
        --           programmable-full threshold (stays below depth).
        ------------------------------------------------------------------
        m_axis_c_tready <= "0000";
        for n in 7 to 7+BURST-1 loop
            drive_beat(Cout, n);
            Cvalid <= '1';
            if n = 7+BURST-1 then
                Ctlast <= '1';            -- mark the very last beat
            end if;
            wait until rising_edge(clk);
        end loop;
        Cvalid <= '0';
        Ctlast <= '0';
        wait for 4*CLK_PERIOD;            -- expect prog_full = '1' here

        -- drain everything; per-PC order checker validates 7..(6+BURST)
        m_axis_c_tready <= "1111";
        wait for (BURST+20)*CLK_PERIOD;
        m_axis_c_tready <= "0000";

        wait for 5*CLK_PERIOD;
        report "=== simulation finished ===" severity note;
        std.env.stop;
        wait;
    end process;

    -- self-check: each PC must emit beats 1,2,3,... in order with the right tag
    MON : process(clk)
        type cnt_arr is array(0 to 3) of integer;
        variable cnt : cnt_arr := (others => 0);
        variable expd : std_logic_vector(255 downto 0);
    begin
        if rising_edge(clk) then
            for i in 0 to 3 loop
                if m_axis_c_tvalid(i) = '1' and m_axis_c_tready(i) = '1' then
                    cnt(i) := cnt(i) + 1;
                    expd := tag256(i*256 + cnt(i));
                    if m_axis_c_tdata((i+1)*256-1 downto i*256) = expd then
                        report "PC " & integer'image(i) & " beat " & integer'image(cnt(i)) &
                               " OK (0x" & to_hex(m_axis_c_tdata(i*256+15 downto i*256)) &
                               ")  tlast=" & std_logic'image(m_axis_c_tlast(i))
                               severity note;
                    else
                        report "PC " & integer'image(i) & " beat " & integer'image(cnt(i)) &
                               " MISMATCH got 0x" & to_hex(m_axis_c_tdata(i*256+15 downto i*256))
                               severity error;
                    end if;
                end if;
            end loop;
        end if;
    end process;

    -- observe prog_full rising edge
    PF_MON : process(clk)
        variable prev : std_logic := '0';
    begin
        if rising_edge(clk) then
            if prog_full = '1' and prev = '0' then
                report "prog_full asserted" severity note;
            end if;
            prev := prog_full;
        end if;
    end process;

end architecture sim;
