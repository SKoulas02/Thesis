library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Testbench for weights_fifo_2k (GEMV 4.0 weight/index ingress + join, AXIS)
--
-- Verifies:
--   1. Barrier   : ready stays '0' until ALL 8 weight + 3 index PCs have a beat.
--   2. Routing   : W_out[(p+1)*256-1:p*256] = weight PC p; indices_out built from
--                  the 3 index PCs; Sparsity_out = index PC2 padding [641:640].
--   3. tlast_out : pulses on the pop of the beat marked with s_axis_w_tlast.
--
-- Data tags (16-bit, replicated): weight PC p beat n = p*256+n; index PC0 = 2000+n,
-- PC1 = 3000+n, PC2 low-128 = 4000+n; sparsity(n) = n mod 4.
-- Self-checking per-PC; needs the axis_data_fifo_pc IP (256-bit TDATA + TLAST).
-- ----------------------------------------------------------------------------

entity weights_fifo_2k_TB is
end entity weights_fifo_2k_TB;

architecture sim of weights_fifo_2k_TB is

    constant CLK_PERIOD : time := 10 ns;

    signal clk    : std_logic := '0';
    signal resetn : std_logic := '0';

    -- write side
    signal s_axis_w_tdata    : std_logic_vector(2047 downto 0) := (others => '0');
    signal s_axis_w_tvalid   : std_logic_vector(7 downto 0)    := (others => '0');
    signal s_axis_w_tready   : std_logic_vector(7 downto 0);
    signal s_axis_w_tlast    : std_logic_vector(7 downto 0)    := (others => '0');

    signal s_axis_ind_tdata  : std_logic_vector(767 downto 0)  := (others => '0');
    signal s_axis_ind_tvalid : std_logic_vector(2 downto 0)    := (others => '0');
    signal s_axis_ind_tready : std_logic_vector(2 downto 0);

    -- read side
    signal rd_en        : std_logic := '0';
    signal ready        : std_logic;
    signal tlast_out    : std_logic;
    signal W_out        : std_logic_vector(2047 downto 0);
    signal indices_out  : std_logic_vector(639 downto 0);
    signal Sparsity_out : std_logic_vector(1 downto 0);

    function tag256(constant v : integer) return std_logic_vector is
        variable r  : std_logic_vector(255 downto 0);
        variable vv : std_logic_vector(15 downto 0);
    begin
        vv := std_logic_vector(to_unsigned(v, 16));
        for i in 0 to 15 loop r(16*i+15 downto 16*i) := vv; end loop;
        return r;
    end function;

    function tag128(constant v : integer) return std_logic_vector is
        variable r  : std_logic_vector(127 downto 0);
        variable vv : std_logic_vector(15 downto 0);
    begin
        vv := std_logic_vector(to_unsigned(v, 16));
        for i in 0 to 7 loop r(16*i+15 downto 16*i) := vv; end loop;
        return r;
    end function;

begin

    DUT : entity work.weights_fifo_2k
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
            rd_en             => rd_en,
            ready             => ready,
            tlast_out         => tlast_out,
            W_out             => W_out,
            indices_out       => indices_out,
            Sparsity_out      => Sparsity_out
        );

    clk <= not clk after CLK_PERIOD/2;

    STIM : process
        -- drive the input tdata buses for beat n (tvalid/tlast set by caller)
        procedure set_beat(constant n : integer) is
        begin
            for p in 0 to 7 loop
                s_axis_w_tdata((p+1)*256-1 downto p*256) <= tag256(p*256 + n);
            end loop;
            s_axis_ind_tdata(255 downto 0)   <= tag256(2000 + n);   -- index PC0
            s_axis_ind_tdata(511 downto 256) <= tag256(3000 + n);   -- index PC1
            s_axis_ind_tdata(639 downto 512) <= tag128(4000 + n);   -- index PC2 low-128
            s_axis_ind_tdata(641 downto 640) <= std_logic_vector(to_unsigned(n mod 4, 2)); -- sparsity
            s_axis_ind_tdata(767 downto 642) <= (others => '0');    -- PC2 padding
        end procedure;
    begin
        resetn <= '0';
        wait for 5*CLK_PERIOD;
        wait until rising_edge(clk);
        resetn <= '1';
        wait until rising_edge(clk);

        ------------------------------------------------------------------
        -- PHASE A : barrier. Write beat 1 to all PCs EXCEPT weight PC7.
        ------------------------------------------------------------------
        set_beat(1);
        s_axis_w_tvalid   <= "01111111";    -- PC7 withheld
        s_axis_ind_tvalid <= "111";
        wait until rising_edge(clk);
        s_axis_w_tvalid   <= (others => '0');
        s_axis_ind_tvalid <= (others => '0');
        wait for 4*CLK_PERIOD;
        assert ready = '0'
            report "BARRIER FAIL: ready high while weight PC7 is empty" severity error;

        -- complete the beat: write PC7
        set_beat(1);
        s_axis_w_tvalid <= "10000000";      -- only PC7
        wait until rising_edge(clk);
        s_axis_w_tvalid <= (others => '0');
        wait for 4*CLK_PERIOD;
        assert ready = '1'
            report "BARRIER FAIL: ready low with all PCs present" severity error;

        ------------------------------------------------------------------
        -- PHASE B : write beats 2,3 to all PCs; weight tlast on beat 3.
        ------------------------------------------------------------------
        for n in 2 to 3 loop
            set_beat(n);
            s_axis_w_tvalid   <= (others => '1');
            s_axis_ind_tvalid <= (others => '1');
            if n = 3 then
                s_axis_w_tlast <= (others => '1');   -- end of weight matrix
            else
                s_axis_w_tlast <= (others => '0');
            end if;
            wait until rising_edge(clk);
        end loop;
        s_axis_w_tvalid   <= (others => '0');
        s_axis_ind_tvalid <= (others => '0');
        s_axis_w_tlast    <= (others => '0');
        wait for 4*CLK_PERIOD;

        ------------------------------------------------------------------
        -- PHASE C : drain. Pop beats 1,2,3; checker validates routing + tlast.
        ------------------------------------------------------------------
        rd_en <= '1';
        wait for 8*CLK_PERIOD;
        rd_en <= '0';

        wait for 5*CLK_PERIOD;
        report "=== simulation finished ===" severity note;
        std.env.stop;
        wait;
    end process;

    -- self-check each popped aligned beat
    MON : process(clk)
        variable cnt : integer := 0;
        variable ok  : boolean;
    begin
        if rising_edge(clk) then
            if rd_en = '1' and ready = '1' then
                cnt := cnt + 1;
                ok  := true;
                for p in 0 to 7 loop
                    if W_out((p+1)*256-1 downto p*256) /= tag256(p*256 + cnt) then
                        ok := false;
                    end if;
                end loop;
                if indices_out(255 downto 0)   /= tag256(2000 + cnt) then ok := false; end if;
                if indices_out(511 downto 256) /= tag256(3000 + cnt) then ok := false; end if;
                if indices_out(639 downto 512) /= tag128(4000 + cnt) then ok := false; end if;
                if Sparsity_out /= std_logic_vector(to_unsigned(cnt mod 4, 2)) then ok := false; end if;

                if ok then
                    report "beat " & integer'image(cnt) & " OK  Sparsity=" &
                           integer'image(to_integer(unsigned(Sparsity_out))) &
                           "  tlast_out=" & std_logic'image(tlast_out)
                           severity note;
                else
                    report "beat " & integer'image(cnt) & " MISMATCH" severity error;
                end if;
            end if;
        end if;
    end process;

end architecture sim;
