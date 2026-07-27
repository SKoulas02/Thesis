library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;


-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
--
-- Description:
-- DENSE variant of c_block -- the innermost compute unit of the dense GEMV
-- baseline. Identical to the sparse c_block (GEMV 4.0) EXCEPT that the index
-- gather is removed:
--
--   sparse : 2 indices drive a 2 x 32:1 mux that picks 2 activations out of the
--            32-element window  (the cost of sparsity support)
--   dense  : the element pair arrives already selected -- A_in is 32 bits
--            (2 elements), the muxes disappear entirely.
--
-- Everything else is byte-identical to the sparse block: 2 multipliers, 1 adder,
-- 1 accumulator (all Xilinx FP IP, all fully pipelined), the valid/tlast
-- sideband pipelining, and the accum_tlast output gate. This is deliberate --
-- the sparse-vs-dense area delta must isolate the gather, nothing else.
--
-- Throughput: 1 beat/cycle = 2 MACs/cycle. The accumulator flushes on tlast_in,
-- so one dot product is produced by asserting tlast_in on its last beat.
-- ----------------------------------------------------------------------------

entity c_block_dense is
    generic(
        EL_SIZE : integer := 16;    -- Bit size of each element
        W_IDX   : integer := 2;     -- Number of matrix elements
        A_IDX   : integer := 2      -- Number of vector elements (dense: the pre-selected pair)
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        W_in        : in std_logic_vector ((EL_SIZE*W_IDX)-1 downto 0);     -- Matrix Input
        A_in        : in std_logic_vector ((EL_SIZE*A_IDX)-1 downto 0);     -- Vector Input (2 elements)

        valid       : in std_logic;
        tlast_in    : in std_logic;

        Cout        : out std_logic_vector (EL_SIZE-1 downto 0);            -- Calculated Output Element
        Cvalid      : out std_logic;
        Ctlast      : out std_logic
    );
end entity c_block_dense;



architecture c_block_dense_arch of c_block_dense is

    component multiplier_wrapper is
    generic(
        EL_SIZE : integer := 16
    );
    port(
        aclk                    : in std_logic;
        aresetn                 : in std_logic;
        s_axis_a_tvalid         : in std_logic;
        s_axis_a_tdata          : in std_logic_vector (EL_SIZE-1 downto 0);
        s_axis_b_tvalid         : in std_logic;
        s_axis_b_tdata          : in std_logic_vector (EL_SIZE-1 downto 0);
        s_axis_b_tlast          : in std_logic;
        m_axis_result_tready    : in std_logic;
        m_axis_result_tvalid    : out std_logic;
        m_axis_result_tdata     : out std_logic_vector (EL_SIZE-1 downto 0);
        m_axis_result_tlast     : out std_logic
    );
    end component multiplier_wrapper;

    component adder_wrapper is
    generic(
        EL_SIZE : integer := 16
    );
    port(
        aclk                    : in std_logic;
        aresetn                 : in std_logic;
        s_axis_a_tvalid         : in std_logic;
        s_axis_a_tdata          : in std_logic_vector (EL_SIZE-1 downto 0);
        s_axis_b_tvalid         : in std_logic;
        s_axis_b_tdata          : in std_logic_vector (EL_SIZE-1 downto 0);
        s_axis_b_tlast          : in std_logic;
        m_axis_result_tready    : in std_logic;
        m_axis_result_tvalid    : out std_logic;
        m_axis_result_tdata     : out std_logic_vector (EL_SIZE-1 downto 0);
        m_axis_result_tlast     : out std_logic
    );
    end component adder_wrapper;

    component accumulator_wrapper is
    generic(
        EL_SIZE : integer := 16
    );
    port(
        aclk                    : in std_logic;
        aresetn                 : in std_logic;
        s_axis_a_tvalid         : in std_logic;
        s_axis_a_tdata          : in std_logic_vector (EL_SIZE-1 downto 0);
        s_axis_a_tlast          : in std_logic;
        m_axis_result_tready    : in std_logic;
        m_axis_result_tvalid    : out std_logic;
        m_axis_result_tdata     : out std_logic_vector (EL_SIZE-1 downto 0);
        m_axis_result_tlast     : out std_logic
    );
    end component accumulator_wrapper;


    signal W_internal           : std_logic_vector ((EL_SIZE*W_IDX)-1 downto 0) := (others => '0');
    signal A_internal           : std_logic_vector ((EL_SIZE*A_IDX)-1 downto 0) := (others => '0');

    signal valid_internal       : std_logic := '0';
    signal tlast_internal       : std_logic := '0';

    type multi_array_type is array (0 to (W_IDX-1)) of std_logic_vector (EL_SIZE-1 downto 0);
    signal multi_array          : multi_array_type := (others => (others => '0'));
    signal multi_valid          : std_logic := '0';
    signal multi_tlast          : std_logic := '0';

    signal adder_valid          : std_logic := '0';
    signal adder_tlast          : std_logic := '0';
    signal adder_data           : std_logic_vector (EL_SIZE-1 downto 0) := (others => '0');

    signal accum_valid          : std_logic := '0';
    signal accum_tlast          : std_logic := '0';
    signal accum_data           : std_logic_vector (EL_SIZE-1 downto 0) := (others => '0');


begin

    MAIN_PROC : process (clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then

                W_internal <= (others => '0');
                A_internal <= (others => '0');

                valid_internal <= '0';
                tlast_internal   <= '0';

                Cout        <= (others => '0');
                Cvalid      <= '0';
                Ctlast      <= '0';

            else

                if accum_tlast = '1' then
                    Cout <= accum_data;
                    Cvalid <= '1';
                    Ctlast <= '1';
                else
                    Cout <= (others => '0');
                    Cvalid <= '0';
                    Ctlast <= '0';
                end if;

                if valid = '1' then

                    W_internal          <= W_in;
                    valid_internal      <= '1';
                    tlast_internal      <= tlast_in;

                    -- DENSE: no gather. The core has already selected this beat's
                    -- element pair, so the window mux of the sparse block is gone.
                    -- Little-endian: A_in[15:0] pairs with w0 = W_in[15:0].
                    A_internal          <= A_in;

                else

                    valid_internal      <= '0';
                    tlast_internal      <= '0';   -- clear the tlast sideband when no beat;
                                                  -- only W_internal / A_internal legitimately hold (data).

                end if;
            end if;
        end if;
    end process MAIN_PROC;

    MULTIPLIERS : for i in 0 to (W_IDX-1) generate

        MULTIPLIER_FIRST : if i = 0 generate

            MULTIPLIER_INST_FIRST : multiplier_wrapper
            generic map(
                EL_SIZE => EL_SIZE
            )
            port map(
                aclk                    => clk,
                aresetn                 => resetn,
                s_axis_a_tvalid         => valid_internal,
                s_axis_a_tdata          => W_internal ((EL_SIZE*(i+1))-1 downto EL_SIZE*i),
                s_axis_b_tvalid         => valid_internal,
                s_axis_b_tdata          => A_internal ((EL_SIZE*(i+1))-1 downto EL_SIZE*i),
                s_axis_b_tlast          => tlast_internal,

                m_axis_result_tready    => '1',          -- always ready: one result per cycle, no backpressure
                m_axis_result_tvalid    => multi_valid,
                m_axis_result_tdata     => multi_array(i),
                m_axis_result_tlast     => multi_tlast
            );
            end generate;

        MULTIPLIER_OTHER : if i /= 0 generate

            MULTIPLIER_INST_SECOND : multiplier_wrapper
            generic map(
                EL_SIZE => EL_SIZE
            )
            port map(
                aclk                    => clk,
                aresetn                 => resetn,
                s_axis_a_tvalid         => valid_internal,
                s_axis_a_tdata          => W_internal ((EL_SIZE*(i+1))-1 downto EL_SIZE*i),
                s_axis_b_tvalid         => valid_internal,
                s_axis_b_tdata          => A_internal ((EL_SIZE*(i+1))-1 downto EL_SIZE*i),
                s_axis_b_tlast          => tlast_internal,

                m_axis_result_tready    => '1',          -- always ready: one result per cycle, no backpressure
                m_axis_result_tvalid    => open,
                m_axis_result_tdata     => multi_array(i),
                m_axis_result_tlast     => open
            );
        end generate;
    end generate;

    ADDER_INSTANCE : adder_wrapper
    generic map(
        EL_SIZE => EL_SIZE
    )
    port map(
        aclk                    => clk,
        aresetn                 => resetn,
        s_axis_a_tvalid         => multi_valid,
        s_axis_a_tdata          => multi_array(0),
        s_axis_b_tvalid         => multi_valid,
        s_axis_b_tdata          => multi_array(1),
        s_axis_b_tlast          => multi_tlast,

        m_axis_result_tready    => '1',          -- always ready: one result per cycle, no backpressure
        m_axis_result_tvalid    => adder_valid,
        m_axis_result_tdata     => adder_data,
        m_axis_result_tlast     => adder_tlast
    );

    ACCUM_INSTANCE : accumulator_wrapper
    generic map(
        EL_SIZE => EL_SIZE
    )
    port map(
        aclk                    => clk,
        aresetn                 => resetn,
        s_axis_a_tvalid         => adder_valid,
        s_axis_a_tdata          => adder_data,
        s_axis_a_tlast          => adder_tlast,
        m_axis_result_tready    => '1',          -- always ready: one result per cycle, no backpressure
        m_axis_result_tvalid    => accum_valid,
        m_axis_result_tdata     => accum_data,
        m_axis_result_tlast     => accum_tlast
    );
end architecture c_block_dense_arch;
