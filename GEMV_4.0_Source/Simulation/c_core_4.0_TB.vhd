library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Testbench for c_core (GEMV 4.0)
--
-- Number format : bfloat16  (1 sign | 8 exp (bias 127) | 7 mantissa)
--
-- Core = 8 x c_block.  Per block i:
--   W_row  slice  = W_row(32*i+31 downto 32*i)        -> W_lo=(15:0), W_hi=(31:16)
--   Indices slice = Indices(10*i+9 downto 10*i)       -> idx_lo=(4:0), idx_hi=(9:5)
--   Cout   lane   = Cout(16*i+15 downto 16*i)
-- Each block: Cout_lane = bf16( W_lo*A(idx_lo) + W_hi*A(idx_hi) ), accumulated.
--
-- LITTLE-ENDIAN gather: activation element k = A_vector(16*k+15 downto 16*k).
-- The accumulator flushes on tlast, so the single-beat test asserts tlast_in.
-- sparsity_lock = '0' so A_vector is loaded (not held).
--
-- IMPORTANT: this TB drives Indices as 80 bits (BLOCKS_NUM*IND_NUM = 8*10).
--   The current c_core_4.0.vhd declares Indices as 160 bits and slices 20 bits
--   per block into a 10-bit c_block port -> width mismatch. Apply the index-path
--   fix (port/signal width -> BLOCKS_NUM*IND_NUM, slice -> (i+1)*IND_NUM..i*IND_NUM)
--   for this TB to elaborate.
-- ----------------------------------------------------------------------------

entity c_core_4_0_TB is
end entity c_core_4_0_TB;

architecture sim of c_core_4_0_TB is

    constant CLK_PERIOD : time := 10 ns;

    type slv16_array is array (natural range <>) of std_logic_vector(15 downto 0);

    -- bf16 encodings of 1.0 .. 16.0 (all exact in bfloat16)
    constant AVAL : slv16_array(0 to 15) := (
        x"3F80", x"4000", x"4040", x"4080", x"40A0", x"40C0", x"40E0", x"4100",
        x"4110", x"4120", x"4130", x"4140", x"4150", x"4160", x"4170", x"4180");

    -- Expected per-lane results: block i = A(2i) + A(2i+1) = (2i+1)+(2i+2) = 4i+3
    --   3,7,11,15,19,23,27,31
    constant EXP : slv16_array(0 to 7) := (
        x"4040", x"40E0", x"4130", x"4170", x"4198", x"41B8", x"41D8", x"41F8");

    constant BF_1 : std_logic_vector(15 downto 0) := x"3F80";  -- 1.0

    -- DUT signals (concrete widths: EL_SIZE=16, W_IDX=16, A_IDX=32, BLOCKS_NUM=8, IND_NUM=10)
    signal clk           : std_logic := '0';
    signal resetn        : std_logic := '0';

    signal W_row         : std_logic_vector(255 downto 0) := (others => '0');
    signal Indices       : std_logic_vector(79 downto 0)  := (others => '0');  -- BLOCKS_NUM*IND_NUM
    signal A_vector      : std_logic_vector(511 downto 0) := (others => '0');

    signal sparsity_lock : std_logic := '0';
    signal valid_in      : std_logic := '0';
    signal tlast_in      : std_logic := '0';

    signal Cout          : std_logic_vector(127 downto 0);
    signal Cvalid        : std_logic;
    signal Ctlast        : std_logic;

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

    -- DUT (generic defaults apply: EL_SIZE=16, W_IDX=16, A_IDX=32, BLOCKS_NUM=8, IND_NUM=10)
    DUT : entity work.c_core
        port map (
            clk           => clk,
            resetn        => resetn,
            W_row         => W_row,
            Indices       => Indices,
            A_vector      => A_vector,
            sparsity_lock => sparsity_lock,
            valid_in      => valid_in,
            tlast_in      => tlast_in,
            Cout          => Cout,
            Cvalid        => Cvalid,
            Ctlast        => Ctlast
        );

    -- clock
    clk <= not clk after CLK_PERIOD/2;

    -- stimulus
    STIM : process
    begin
        -- reset
        resetn        <= '0';
        valid_in      <= '0';
        sparsity_lock <= '0';
        wait for 5*CLK_PERIOD;
        wait until rising_edge(clk);
        resetn <= '1';
        wait until rising_edge(clk);

        -- Build the activation vector: element k = (k+1).0, little-endian.
        A_vector <= (others => '0');
        for k in 0 to 15 loop
            A_vector(16*k+15 downto 16*k) <= AVAL(k);
        end loop;

        -- All 16 weights = 1.0 (so each lane is a plain A(idx_lo)+A(idx_hi) sum).
        W_row <= (others => '0');
        for k in 0 to 15 loop
            W_row(16*k+15 downto 16*k) <= BF_1;
        end loop;

        -- Block i picks elements 2i (idx_lo / W_lo) and 2i+1 (idx_hi / W_hi).
        Indices <= (others => '0');
        for i in 0 to 7 loop
            Indices(10*i+4 downto 10*i)   <= std_logic_vector(to_unsigned(2*i,   5)); -- idx_lo
            Indices(10*i+9 downto 10*i+5) <= std_logic_vector(to_unsigned(2*i+1, 5)); -- idx_hi
        end loop;

        wait until rising_edge(clk);

        -- single-beat transaction, tlast flushes every lane's accumulator
        sparsity_lock <= '0';                -- load A_vector
        valid_in      <= '1';
        tlast_in      <= '1';
        wait until rising_edge(clk);
        valid_in      <= '0';
        tlast_in      <= '0';

        -- let mul -> add -> accum drain across all blocks
        wait for 30*CLK_PERIOD;

        report "=== simulation finished ===" severity note;
        std.env.stop;
        wait;
    end process;

    -- monitor: when a result is produced, check all 8 lanes
    MON : process(clk)
        variable lane : std_logic_vector(15 downto 0);
    begin
        if rising_edge(clk) then
            if Cvalid = '1' then
                report "Cout = 0x" & to_hex(Cout) & "   Ctlast=" & std_logic'image(Ctlast)
                       severity note;
                for i in 0 to 7 loop
                    lane := Cout(16*i+15 downto 16*i);
                    if lane = EXP(i) then
                        report "  lane " & integer'image(i) & " = 0x" & to_hex(lane) & "  PASS"
                               severity note;
                    else
                        report "  lane " & integer'image(i) & " = 0x" & to_hex(lane) &
                               "  expected 0x" & to_hex(EXP(i)) & "  FAIL"
                               severity error;
                    end if;
                end loop;
            end if;
        end if;
    end process;

end architecture sim;
