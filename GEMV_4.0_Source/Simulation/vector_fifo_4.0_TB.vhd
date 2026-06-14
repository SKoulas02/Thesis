library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Testbench for vector_fifo_512 (GEMV 4.0 activation ingress + join)
--
-- Exercises the three behaviours that matter in this module:
--   1. Skew barrier : ready stays '0' until BOTH PC FIFOs hold a beat.
--   2. Join order   : A_vector_out = { PC1_data , PC0_data } (PC k -> [k*256 +: 256]).
--   3. tlast        : tlast_out asserts only when every head's tlast bit is set.
--
-- Observation-based (the FWFT FIFO's empty-deassert latency is IP-config
-- dependent): a monitor prints A_vector_out / tlast_out whenever ready='1'.
-- Requires the fifo_gen_vector IP (257-bit, 64-deep, FWFT) in the project.
-- ----------------------------------------------------------------------------

entity vector_fifo_4_0_TB is
end entity vector_fifo_4_0_TB;

architecture sim of vector_fifo_4_0_TB is

    constant CLK_PERIOD : time := 10 ns;

    -- DUT signals (PC_WIDTH=256, A_PCS=2)
    signal clk          : std_logic := '0';
    signal resetn       : std_logic := '0';

    signal a_din        : std_logic_vector(511 downto 0) := (others => '0');
    signal a_wr         : std_logic_vector(1 downto 0)   := (others => '0');
    signal a_tlast      : std_logic_vector(1 downto 0)   := (others => '0');
    signal a_full       : std_logic_vector(1 downto 0);

    signal rd_en        : std_logic := '0';
    signal ready        : std_logic;
    signal A_vector_out : std_logic_vector(511 downto 0);
    signal tlast_out    : std_logic;

    -- 256-bit beat = sixteen copies of a 16-bit tag (easy to read in waveform)
    function rep16(constant v : std_logic_vector(15 downto 0)) return std_logic_vector is
        variable r : std_logic_vector(255 downto 0);
    begin
        for i in 0 to 15 loop
            r(16*i+15 downto 16*i) := v;
        end loop;
        return r;
    end function;

    function tag(constant n : integer) return std_logic_vector is
    begin
        return rep16(std_logic_vector(to_unsigned(n, 16)));
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

    DUT : entity work.vector_fifo_512
        port map (
            clk          => clk,
            resetn       => resetn,
            a_din        => a_din,
            a_wr         => a_wr,
            a_tlast      => a_tlast,
            a_full       => a_full,
            rd_en        => rd_en,
            ready        => ready,
            A_vector_out => A_vector_out,
            tlast_out    => tlast_out
        );

    clk <= not clk after CLK_PERIOD/2;

    STIM : process
    begin
        -- reset
        resetn <= '0';
        a_wr   <= "00";
        a_tlast<= "00";
        rd_en  <= '0';
        wait for 5*CLK_PERIOD;
        wait until rising_edge(clk);
        resetn <= '1';
        wait until rising_edge(clk);

        ------------------------------------------------------------------
        -- PHASE A : skew barrier
        --   write PC0 beat0 only -> ready must stay '0' (PC1 empty)
        ------------------------------------------------------------------
        a_din(255 downto 0) <= tag(1);          -- PC0 beat0 = 0x0001...
        a_wr   <= "01";                         -- write PC0 only
        a_tlast<= "00";
        wait until rising_edge(clk);
        a_wr   <= "00";
        wait for 4*CLK_PERIOD;                  -- expect ready = '0' here

        -- now write PC1 beat0 -> both present, ready -> '1'
        a_din(511 downto 256) <= tag(257);      -- PC1 beat0 = 0x0101...
        a_wr   <= "10";                         -- write PC1 only
        wait until rising_edge(clk);
        a_wr   <= "00";
        wait for 4*CLK_PERIOD;
        -- expect: ready='1', A_vector_out = {0x0101..., 0x0001...}, tlast_out='0'

        -- pop beat0
        rd_en <= '1';
        wait until rising_edge(clk);
        rd_en <= '0';
        wait for 4*CLK_PERIOD;

        ------------------------------------------------------------------
        -- PHASE B : lockstep writes + tlast on the final beat
        --   beats 1,2,3 to both PCs; beat 3 carries tlast on both PCs
        ------------------------------------------------------------------
        for n in 1 to 3 loop
            a_din(255 downto 0)   <= tag(n+1);        -- PC0 = 2,3,4
            a_din(511 downto 256) <= tag(n+257);      -- PC1 = 0x0102,0103,0104
            a_wr <= "11";
            if n = 3 then
                a_tlast <= "11";                      -- last beat of the vector
            else
                a_tlast <= "00";
            end if;
            wait until rising_edge(clk);
        end loop;
        a_wr    <= "00";
        a_tlast <= "00";
        wait for 4*CLK_PERIOD;

        -- drain: pop the 3 beats; tlast_out must pulse on the 3rd
        rd_en <= '1';
        wait for 6*CLK_PERIOD;
        rd_en <= '0';

        wait for 10*CLK_PERIOD;
        report "=== simulation finished ===" severity note;
        std.env.stop;
        wait;
    end process;

    -- monitor: report each presented beat and flag tlast
    MON : process(clk)
    begin
        if rising_edge(clk) then
            if ready = '1' then
                report "ready  A_vector_out = 0x" & to_hex(A_vector_out) &
                       "   tlast_out=" & std_logic'image(tlast_out)
                       severity note;
            end if;
        end if;
    end process;

end architecture sim;
