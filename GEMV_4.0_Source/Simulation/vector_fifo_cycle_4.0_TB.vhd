library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Testbench for vector_cycle_512 (GEMV 4.0 activation replay buffer)
--
-- Now exercises REUSE via the recirculation path:
--   LOAD   (cycle_en='0'): write the vector in with valid_in, rd_en held '0'.
--   REPLAY (cycle_en='1'): each rd_en pops the head AND writes it back, so the
--                          same beats recirculate. The vector should reappear in
--                          order on every lap WITHOUT re-writing it.
--
-- Checks:
--   1. During replay, A_vector_out cycles 1,2,3,4, 1,2,3,4, ... (reuse works).
--   2. tlast_out pulses on tag 4 once per lap (lap-boundary marker).
--
-- Requires the fifo_gen_vector_cycle IP configured FIRST-WORD-FALL-THROUGH and
-- sized ABOVE the vector (e.g. 128 deep for 64 beats) so it is never full
-- during recirc -- otherwise the simultaneous read+write-back is dropped.
-- Observation-based: a monitor prints each consumed beat.
-- ----------------------------------------------------------------------------

entity vector_cycle_512_TB is
end entity vector_cycle_512_TB;

architecture sim of vector_cycle_512_TB is

    constant CLK_PERIOD : time := 10 ns;

    -- DUT signals (PC_WIDTH=256, A_PCS=2)
    signal clk          : std_logic := '0';
    signal resetn       : std_logic := '0';

    signal A_vector     : std_logic_vector(511 downto 0) := (others => '0');
    signal tlast_in     : std_logic := '0';
    signal valid_in     : std_logic := '0';
    signal rd_en        : std_logic := '0';
    signal cycle_en     : std_logic := '0';

    signal ready        : std_logic;
    signal A_vector_out : std_logic_vector(511 downto 0);
    signal tlast_out    : std_logic;

    -- 512-bit beat = thirty-two copies of a 16-bit tag (easy to read)
    function tag(constant n : integer) return std_logic_vector is
        variable r : std_logic_vector(511 downto 0);
        variable v : std_logic_vector(15 downto 0);
    begin
        v := std_logic_vector(to_unsigned(n, 16));
        for i in 0 to 31 loop
            r(16*i+15 downto 16*i) := v;
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

begin

    DUT : entity work.vector_cycle_512
        port map (
            clk          => clk,
            resetn       => resetn,
            A_vector     => A_vector,
            tlast_in     => tlast_in,
            valid_in     => valid_in,
            rd_en        => rd_en,
            cycle_en     => cycle_en,
            ready        => ready,
            A_vector_out => A_vector_out,
            tlast_out    => tlast_out
        );

    clk <= not clk after CLK_PERIOD/2;

    STIM : process
    begin
        -- reset
        resetn   <= '0';
        valid_in <= '0';
        rd_en    <= '0';
        cycle_en <= '0';
        tlast_in <= '0';
        wait for 5*CLK_PERIOD;
        wait until rising_edge(clk);
        resetn <= '1';
        wait until rising_edge(clk);

        ------------------------------------------------------------------
        -- LOAD phase (cycle_en='0', rd_en='0'): write 4 beats (tags 1..4),
        -- tlast on the last beat only.
        ------------------------------------------------------------------
        cycle_en <= '0';
        rd_en    <= '0';
        for n in 1 to 4 loop
            A_vector <= tag(n);
            valid_in <= '1';
            if n = 4 then
                tlast_in <= '1';      -- end-of-vector marker
            else
                tlast_in <= '0';
            end if;
            wait until rising_edge(clk);
        end loop;
        valid_in <= '0';
        tlast_in <= '0';
        wait for 4*CLK_PERIOD;        -- let the loaded beats settle (FWFT fill)

        ------------------------------------------------------------------
        -- REPLAY phase (cycle_en='1'): recirculate. Each rd_en pops + writes
        -- back, so tags 1,2,3,4 should repeat for as long as we read.
        -- 14 reads ~= 3+ laps of the 4-beat vector.
        ------------------------------------------------------------------
        cycle_en <= '1';
        rd_en    <= '1';
        wait for 14*CLK_PERIOD;

        -- stall mid-replay: rd_en low -> recirc must freeze (occupancy held),
        -- then resume; the sequence must continue uncorrupted.
        rd_en <= '0';
        wait for 3*CLK_PERIOD;
        rd_en <= '1';
        wait for 8*CLK_PERIOD;
        rd_en <= '0';

        wait for 5*CLK_PERIOD;
        report "=== simulation finished ===" severity note;
        std.env.stop;
        wait;
    end process;

    -- monitor: report each beat actually consumed (rd_en & ready)
    MON : process(clk)
    begin
        if rising_edge(clk) then
            if rd_en = '1' and ready = '1' then
                report "pop A_vector_out = 0x" & to_hex(A_vector_out) &
                       "   tlast_out=" & std_logic'image(tlast_out)
                       severity note;
            end if;
        end if;
    end process;

end architecture sim;
