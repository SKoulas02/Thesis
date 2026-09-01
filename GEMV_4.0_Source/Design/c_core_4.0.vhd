library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
-- 
-- Description:
-- This Module is used to calculate the output of a row of 2:N GEMV operations with variable sparsity.
-- Input: 2 elements of Weight Matrix W and 32 elements of Activation Vector A.
-- It is made up of c blocks that are fully pipelined.
-- The module can be reused for different GEMV operations and sparsity patterns
-- It has a through-put of 1 cycle.
-- ----------------------------------------------------------------------------

entity c_core is
    generic(
        EL_SIZE     : integer := 16;    -- Bit size of each element
        W_IDX       : integer := 16;    -- Number of matrix elements
        A_IDX       : integer := 32;    -- Number of vector elements
        BLOCKS_NUM  : integer := 8;     -- Number of c blocks (2 elements each)
        IND_NUM     : integer := 10     -- Number of Indices Bits
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;
        
        W_row       : in std_logic_vector ((W_IDX*EL_SIZE)-1 downto 0);
        Indices     : in std_logic_vector ((BLOCKS_NUM*IND_NUM)-1 downto 0);
        A_vector    : in std_logic_vector ((A_IDX*EL_SIZE)-1 downto 0);

        sparsity_lock   : in std_logic; -- Sparsity Lock Signal for correct calculations

        valid_in    : in std_logic;
        tlast_in    : in std_logic;
        
        Cout        : out std_logic_vector ((EL_SIZE*BLOCKS_NUM)-1 downto 0);
        Cvalid      : out std_logic;
        Ctlast      : out std_logic
    );
end entity c_core;


architecture c_core_arch of c_core is

    component c_block is
    generic(
        EL_SIZE : integer := 16;    -- Bit size of each element
        W_IDX   : integer := 2;     -- Number of matrix elements
        A_IDX   : integer := 32;    -- Number of vector elements
        IND_NUM : integer := 10     -- Number of Indices Bits
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        W_in        : in std_logic_vector ((EL_SIZE*W_IDX)-1 downto 0);     -- Matrix Input
        Indices     : in std_logic_vector (IND_NUM-1 downto 0);             -- Indices Input
        A_in        : in std_logic_vector ((EL_SIZE*A_IDX)-1 downto 0);     -- Vector Input
        
        valid       : in std_logic;                                     
        tlast_in    : in std_logic;

        Cout        : out std_logic_vector (EL_SIZE-1 downto 0);            -- Calculated Output Element
        Cvalid      : out std_logic;
        Ctlast      : out std_logic
    );
    end component c_block;

    signal W_internal       : std_logic_vector ((EL_SIZE*W_IDX)-1 downto 0);
    signal A_internal       : std_logic_vector ((EL_SIZE*A_IDX)-1 downto 0);
    signal Indices_internal : std_logic_vector ((BLOCKS_NUM*IND_NUM)-1 downto 0);

    signal valid_internal   : std_logic;
    signal tlast_internal   : std_logic;

    -- ------------------------------------------------------------------------
    -- KEEP ONE ACTIVATION-WINDOW REGISTER PER CORE.
    --
    -- All 8 cores latch the SAME A_vector under the SAME enable, so the eight
    -- A_internal registers are logically identical and Vivado's
    -- equivalent-register removal collapses them into ONE physical register.
    -- By its own metric that is a win (it saves ~3,584 FFs out of 2.6 M); for
    -- routing it is a disaster. Measured on the 250 MHz system build:
    --
    --   net A_in[109]  fo=128  ->  3.246 ns
    --   critical path  3.758 ns total: logic 0.269 ns (7%), route 3.489 ns (93%)
    --
    -- fo=128 is exactly 64 blocks x 2 gather muxes -- i.e. every block in the
    -- design hanging off one register bit, spread across the die. With the eight
    -- copies kept apart each drives only its own 8 blocks x 2 muxes = 16 loads
    -- and can be placed beside the core it feeds.
    --
    -- equivalent_register_removal, NOT dont_touch: this blocks only the
    -- cross-core merge and leaves every other optimisation (including the
    -- replication that max_fanout asks for) available.
    --
    -- Both are SYNTHESIS attributes and are inert in XSim, so simulation
    -- semantics -- and every co-simulation result already banked -- are
    -- unchanged. This is not a functional edit.
    -- ------------------------------------------------------------------------
    attribute equivalent_register_removal : string;
    attribute equivalent_register_removal of A_internal : signal is "no";

    attribute max_fanout : integer;
    attribute max_fanout of A_internal : signal is 16;

begin

    MAIN_PROC : process(clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then
                
                W_internal <= (others => '0');
                A_internal <= (others => '0');
                Indices_internal <= (others => '0');

                valid_internal <= '0';
                tlast_internal <= '0';

            else
                
                if valid_in = '1' then

                    if sparsity_lock = '0' then

                    A_internal <= A_vector;
                    
                    end if;
                
                    valid_internal <= '1';
                    tlast_internal <= tlast_in;

                    W_internal <= W_row;
                    Indices_internal <= Indices;

                else

                    valid_internal <= '0';
                    tlast_internal <= '0';

                end if;
            end if;
        end if;
    end process MAIN_PROC;

    C_CORE : for i in 0 to BLOCKS_NUM-1 generate
        C_BLOCK_FIRST : if i = 0 generate
            C_BLOCK_INST_FIRST : c_block
            generic map (
                EL_SIZE => EL_SIZE,
                W_IDX => 2,
                A_IDX => A_IDX,
                IND_NUM => IND_NUM
            )
            port map (
                clk => clk,
                resetn => resetn,

                W_in => W_internal(((i+1)*2*EL_SIZE)-1 downto (i*2*EL_SIZE)),
                Indices => Indices_internal(((i+1)*IND_NUM)-1 downto (i*IND_NUM)),
                A_in => A_internal,

                valid => valid_internal,
                tlast_in => tlast_internal,

                Cout => Cout(((i+1)*EL_SIZE)-1 downto (i*EL_SIZE)),
                Cvalid => Cvalid,
                Ctlast => Ctlast
            );
        end generate C_BLOCK_FIRST;
        
        C_BLOCK_REST : if i /= 0 generate
            C_BLOCK_INST_REST : c_block
                generic map (
                    EL_SIZE => EL_SIZE,
                    W_IDX => 2,
                    A_IDX => A_IDX,
                    IND_NUM => IND_NUM
                )
                port map (
                    clk => clk,
                    resetn => resetn,

                    W_in => W_internal(((i+1)*2*EL_SIZE)-1 downto (i*2*EL_SIZE)),
                    Indices => Indices_internal(((i+1)*IND_NUM)-1 downto (i*IND_NUM)),
                    A_in => A_internal,

                    valid => valid_internal,
                    tlast_in => tlast_internal,

                    Cout => Cout(((i+1)*EL_SIZE)-1 downto (i*EL_SIZE)),
                    Cvalid => open,
                    Ctlast => open
                );
        end generate C_BLOCK_REST;
    end generate C_CORE;

end architecture c_core_arch;