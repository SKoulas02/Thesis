library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Testbench for c_block (GEMV 4.0)
--
-- Number format : bfloat16  (1 sign | 8 exp (bias 127) | 7 mantissa)
--
-- Hardware datapath (matches the current RTL):
--   2 multipliers -> 1 adder -> 1 accumulator.
--   The accumulator sums every 'valid' beat and outputs on tlast, so each
--   test below asserts tlast_in on its single beat to flush one dot product.
--
--   Cout = bf16(  W_lo * A_word(Indices(4 downto 0))
--               + W_hi * A_word(Indices(9 downto 5)) )
--   where  W_lo = W_in(15 downto 0),  W_hi = W_in(31 downto 16)
--   and (LITTLE-ENDIAN gather):
--          A_word(k) = A_in(16*k+15 downto 16*k)   -- element 0 at the LSB end
--
-- NOTE: requires the Multiplier / Adder / Accumulator IP cores in the project.
-- ----------------------------------------------------------------------------

entity c_block_4_0_TB is
end entity c_block_4_0_TB;

architecture sim of c_block_4_0_TB is

    constant CLK_PERIOD : time := 10 ns;

    -- bfloat16 encodings of the exact small values used by the tests
    constant BF_NEG2 : std_logic_vector(15 downto 0) := x"C000";  -- -2.0
    constant BF_1    : std_logic_vector(15 downto 0) := x"3F80";  --  1.0
    constant BF_2    : std_logic_vector(15 downto 0) := x"4000";  --  2.0
    constant BF_3    : std_logic_vector(15 downto 0) := x"4040";  --  3.0
    constant BF_4    : std_logic_vector(15 downto 0) := x"4080";  --  4.0
    constant BF_5    : std_logic_vector(15 downto 0) := x"40A0";  --  5.0
    constant BF_7    : std_logic_vector(15 downto 0) := x"40E0";  --  7.0

    -- DUT signals (concrete widths: EL_SIZE=16, W_IDX=2, A_IDX=32, IND_NUM=10)
    signal clk      : std_logic := '0';
    signal resetn   : std_logic := '0';

    signal W_in     : std_logic_vector(31 downto 0)  := (others => '0');
    signal Indices  : std_logic_vector(9 downto 0)   := (others => '0');
    signal A_in     : std_logic_vector(511 downto 0) := (others => '0');

    signal valid    : std_logic := '0';
    signal tlast_in : std_logic := '0';

    signal Cout     : std_logic_vector(15 downto 0);
    signal Cvalid   : std_logic;
    signal Ctlast   : std_logic;

    -- compact hex printer for report messages
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

    -- DUT (generic defaults apply: EL_SIZE=16, W_IDX=2, A_IDX=32, IND_NUM=10)
    DUT : entity work.c_block
        port map (
            clk      => clk,
            resetn   => resetn,
            W_in     => W_in,
            Indices  => Indices,
            A_in     => A_in,
            valid    => valid,
            tlast_in => tlast_in,
            Cout     => Cout,
            Cvalid   => Cvalid,
            Ctlast   => Ctlast
        );

    -- clock
    clk <= not clk after CLK_PERIOD/2;

    -- stimulus
    STIM : process
    begin
        -- reset
        resetn <= '0';
        valid  <= '0';
        wait for 5*CLK_PERIOD;
        wait until rising_edge(clk);
        resetn <= '1';
        wait until rising_edge(clk);

        -- preload the activation vector (little-endian: element k at A_in(16k+15 : 16k))
        A_in <= (others => '0');
        A_in(16*0+15 downto 16*0) <= BF_5;   -- element 0 = 5.0
        A_in(16*1+15 downto 16*1) <= BF_7;   -- element 1 = 7.0
        A_in(16*2+15 downto 16*2) <= BF_4;   -- element 2 = 4.0
        A_in(16*3+15 downto 16*3) <= BF_4;   -- element 3 = 4.0
        wait until rising_edge(clk);

        ------------------------------------------------------------------
        -- TEST 1 :  W_lo=2.0 * elem(idx_lo=0)=5.0  +  W_hi=3.0 * elem(idx_hi=1)=7.0
        --           = 10.0 + 21.0 = 31.0   -> bf16 = 0x41F8
        ------------------------------------------------------------------
        W_in     <= BF_3 & BF_2;             -- (31:16)=W_hi=3.0 , (15:0)=W_lo=2.0
        Indices  <= "00001" & "00000";       -- idx_hi=1 , idx_lo=0
        valid    <= '1';
        tlast_in <= '1';                     -- flush this single-beat dot product
        wait until rising_edge(clk);
        valid    <= '0';
        tlast_in <= '0';
        wait for 30*CLK_PERIOD;              -- let mul->add->accum drain

        ------------------------------------------------------------------
        -- TEST 2 :  1.0*elem(2)=4.0 + 1.0*elem(3)=4.0 = 8.0  -> bf16 = 0x4100
        ------------------------------------------------------------------
        W_in     <= BF_1 & BF_1;
        Indices  <= "00011" & "00010";       -- idx_hi=3 , idx_lo=2
        valid    <= '1';
        tlast_in <= '1';
        wait until rising_edge(clk);
        valid    <= '0';
        tlast_in <= '0';
        wait for 30*CLK_PERIOD;

        ------------------------------------------------------------------
        -- TEST 3 :  W_lo=-2.0*elem(0)=5.0 + W_hi=1.0*elem(0)=5.0
        --           = -10.0 + 5.0 = -5.0   -> bf16 = 0xC0A0
        ------------------------------------------------------------------
        W_in     <= BF_1 & BF_NEG2;          -- W_hi=1.0 , W_lo=-2.0
        Indices  <= "00000" & "00000";       -- both pick element 0 = 5.0
        valid    <= '1';
        tlast_in <= '1';
        wait until rising_edge(clk);
        valid    <= '0';
        tlast_in <= '0';
        wait for 30*CLK_PERIOD;

        report "=== simulation finished ===" severity note;
        std.env.stop;
        wait;
    end process;

    -- monitor: print every produced C result
    MON : process(clk)
    begin
        if rising_edge(clk) then
            if Cvalid = '1' then
                report "Cout = 0x" & to_hex(Cout) &
                       "   Ctlast=" & std_logic'image(Ctlast)
                       severity note;
            end if;
        end if;
    end process;

end architecture sim;
