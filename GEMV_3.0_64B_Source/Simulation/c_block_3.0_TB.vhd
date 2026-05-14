library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity c_block_TB is
end entity c_block_TB;

architecture c_block_TB_arch of c_block_TB is

    component c_block is
    --     generic(
    --     EL_SIZE : integer := 16;    -- Bit size of each element
    --     A_IDX   : integer := 2;     -- Number of matrix elements
    --     B_IDX   : integer := 4;     -- Number of vector elements
    --     IND_NUM : integer := 4      -- Number of Indices Bits
    -- );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        A_in        : in std_logic_vector ((16*2)-1 downto 0);     -- Matrix Input
        Indices     : in std_logic_vector (4-1 downto 0);             -- Indices Input
        B_in        : in std_logic_vector ((16*4)-1 downto 0);     -- Vector Input
        
        valid       : in std_logic;
        block_flag  : in std_logic;                                         -- Stop Flag
        tlast_in    : in std_logic;

        Bout        : out std_logic_vector ((16*4)-1 downto 0);    -- Vector Output
        tlast_out   : out std_logic;
        valid_out   : out std_logic;

        Cout        : out std_logic_vector (16-1 downto 0);            -- Calculated Output Element
        Cvalid      : out std_logic;
        Ctlast      : out std_logic
    );
    end component c_block;

    constant CLK_PERIOD : time := 10 ns;
    constant EL_SIZE    : integer := 16;
    constant A_IDX      : integer := 2;
    constant B_IDX      : integer := 4;
    constant IND_NUM    : integer := 4;

    signal clk         : std_logic := '0';
    signal resetn      : std_logic := '0';
    signal A_in        : std_logic_vector ((EL_SIZE*A_IDX*2)-1 downto 0) := (others => '0');
    signal Indices     : std_logic_vector ((IND_NUM*2)-1 downto 0) := (others => '0');
    signal B_in        : std_logic_vector ((EL_SIZE*B_IDX)-1 downto 0) := (others => '0');
    signal valid       : std_logic := '0';
    signal block_flag  : std_logic := '0';
    signal tlast_in    : std_logic := '0';
    
    signal Cout1       : std_logic_vector (EL_SIZE-1 downto 0);
    signal Cvalid1      : std_logic;
    signal Ctlast1      : std_logic;

    signal Cout2       : std_logic_vector (EL_SIZE-1 downto 0);
    signal Cvalid2      : std_logic;
    signal Ctlast2      : std_logic;

    signal Bout1       : std_logic_vector ((EL_SIZE*B_IDX)-1 downto 0);
    signal valid1      : std_logic;
    signal tlast1      : std_logic;

    signal Bout2       : std_logic_vector ((EL_SIZE*B_IDX)-1 downto 0);
    signal valid2      : std_logic;
    signal tlast2      : std_logic;

    signal Bout3       : std_logic_vector ((EL_SIZE*B_IDX)-1 downto 0);
    signal valid3      : std_logic;
    signal tlast3      : std_logic;

    signal Cout3       : std_logic_vector (EL_SIZE-1 downto 0);
    signal Cvalid3      : std_logic;
    signal Ctlast3      : std_logic;
begin

    CLK_PROCESS : process
    begin
        clk <= '1';
        wait for CLK_PERIOD/2;
        clk <= '0';
        wait for CLK_PERIOD/2;
    end process;

    DUT : c_block
        port map (
        clk         => clk,
        resetn      => resetn,
        A_in        => A_in((EL_SIZE*A_IDX)-1 downto 0),
        Indices     => Indices((IND_NUM)-1 downto 0),
        B_in        => B_in,
        valid       => valid,
        block_flag  => block_flag,
        tlast_in    => tlast_in,
        Bout        => Bout1,
        tlast_out   => tlast1,
        valid_out   => valid1,
        Cout        => Cout1,
        Cvalid      => Cvalid1,
        Ctlast      => Ctlast1
    );

    DUT2: c_block
        port map (
        clk         => clk,
        resetn      => resetn,
        A_in        => A_in((EL_SIZE*A_IDX*2)-1 downto EL_SIZE*A_IDX),
        Indices     => Indices((IND_NUM*2)-1 downto IND_NUM),
        B_in        => Bout1,
        valid       => valid1,
        block_flag  => block_flag,
        tlast_in    => tlast1,
        Bout        => Bout2,
        tlast_out   => tlast2,
        valid_out   => valid2   ,
        Cout        => Cout2,
        Cvalid      => Cvalid2,
        Ctlast      => Ctlast2  
    );

    DUT3: c_block
        port map (
        clk         => clk,
        resetn      => resetn,
        A_in        => A_in((EL_SIZE*A_IDX*2)-1 downto EL_SIZE*A_IDX),
        Indices     => Indices((IND_NUM*2)-1 downto IND_NUM),
        B_in        => Bout2,
        valid       => valid2,
        block_flag  => block_flag,
        tlast_in    => tlast2,
        Bout        => Bout3,
        tlast_out   => tlast3,
        valid_out   => valid3,
        Cout        => Cout3,
        Cvalid      => Cvalid3,
        Ctlast      => Ctlast3
    );

    STIMULUS : process
    begin

        -- Initial state
        resetn     <= '0';
        block_flag <= '0';
        valid      <= '0';
        tlast_in   <= '0';
        A_in       <= (others => '0');
        Indices    <= (others => '0');
        B_in       <= (others => '0');
        wait for clk_period*20;
        resetn <= '1';
        wait for clk_period*20;

        -- =============================================================
        -- Test 1: two-beat accumulation, no block_flag
        --   A_in[31:0]  = 0x40404080 -> DUT1 A = [4.0, 3.0]   (a0=bits15:0)
        --   A_in[63:32] = 0x3F804000 -> DUT2 A = [2.0, 1.0]
        --   B_in        = 0x3F8040E0404040C0 -> B = [6.0, 3.0, 7.0, 1.0]
        --   Indices[3:0] = "0000" -> DUT1 picks b0,b0
        --   Indices[7:4] = "0001" -> DUT2 picks b1,b0
        -- Per-beat dot products:
        --   DUT1: 4.0*6.0 + 3.0*6.0 = 42.0
        --   DUT2: 2.0*3.0 + 1.0*6.0 = 12.0
        -- valid is held high for 2 rising edges, tlast on the 2nd:
        --   Cout1 = 84.0 -> bfloat16 0x42A8
        --   Cout2 = 24.0 -> bfloat16 0x41C0
        -- =============================================================
        A_in     <= x"3F80400040404080";
        Indices  <= "00010000";
        B_in     <= x"3F8040E0404040C0";
        valid    <= '1';
        tlast_in <= '0';
        wait for clk_period;
        tlast_in <= '1';
        wait for clk_period;
        tlast_in <= '0';
        valid    <= '0';

        -- Allow pipeline to flush before next test
        wait for clk_period*40;

        -- =============================================================
        -- Test 2: block_flag freeze across a two-beat accumulation
        --   A_in[31:0]  = 0x40004000 -> DUT1 A = [2.0, 2.0]
        --   A_in[63:32] = 0x40004000 -> DUT2 A = [2.0, 2.0]
        --   B_in        = 0x3F803F803F803F80 -> B = [1.0, 1.0, 1.0, 1.0]
        --   Indices     = "00000000" -> both DUTs pick b0,b0
        -- Per-beat dot product (both DUTs): 2.0*1.0 + 2.0*1.0 = 4.0
        --
        -- Sequence:
        --   beat 1 : valid=1, tlast=0
        --   stall  : block_flag=1 for 8 cycles -> Cvalid stays low,
        --                                         pipeline state frozen
        --   beat 2 : valid=1, tlast=1
        --
        -- Two beats accumulated:
        --   Cout1 = 8.0 -> bfloat16 0x4100
        --   Cout2 = 8.0 -> bfloat16 0x4100
        -- =============================================================
        A_in     <= x"4000400040004000";
        Indices  <= "00000000";
        B_in     <= x"3F803F803F803F80";

        -- Beat 1
        valid    <= '1';
        tlast_in <= '0';
        wait for clk_period;
        valid    <= '0';
        A_in     <= (others => '0');
        B_in     <= (others => '0');
        Indices  <= (others => '0');
        -- Stall the pipeline while data is in flight inside the IP cores
        -- wait for clk_period*2;
        block_flag <= '1';
        wait for clk_period*8;
        block_flag <= '0';
        -- wait for clk_period*2;
        B_in     <= x"4000_4000_4000_4000";
        A_in     <= x"4040_4040_4040_4040";
        Indices  <= "11010101";
        -- Beat 2 with tlast
        valid    <= '1';
        tlast_in <= '1';
        wait for clk_period;
        valid    <= '0';
        tlast_in <= '0';
        -- A_in     <= (others => '0');
        B_in     <= (others => '0');
        Indices  <= (others => '0');
        -- Allow pipeline to flush and observe final Cvalid/Cout
        wait for clk_period*2;

        A_in     <= (others => '0');
        wait;
    end process STIMULUS;
end architecture c_block_TB_arch;
    