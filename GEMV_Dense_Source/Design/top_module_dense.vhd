library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
--
-- Description:
-- DENSE GEMV top module -- the comparison baseline for the 2:M sparse engine
-- (two2N / GEMV 4.0). Same skeleton, same FIFOs, same accumulator semantics,
-- same global gate; the sparsity machinery is removed:
--
--   * freeze length is a CONSTANT 16 (= A_IDX/2 beats per 32-element window,
--     2 MACs per block per cycle) instead of the runtime 32/M counter,
--   * the 3 index PCs and the 2-bit Sparsity code are gone (17 HBM PCs -> 14),
--   * the per-block gather is gone -- c_core_dense shifts one shared element
--     pair out of the window each cycle,
--   * the whole 2:32 no-freeze special case (flush_2to32 / sparsity_next
--     exceptions) disappears, because dense always freezes.
--
-- Everything else is carried over verbatim from the verified sparse top: the
-- ingress 1-deep register stages (FWFT drain-hazard fix), the activation replay
-- buffer, the c_fifo output fork, the prog_full global gate, and the two-tlast
-- discipline (activation tlast = per-lap accumulator flush, weight tlast =
-- end-of-calculation, aligned with the drained output).
--
-- Per lap: 64 output rows, V/2 weight beats for a V-element vector.
-- ----------------------------------------------------------------------------

entity dense_gemv is
    generic(
        EL_SIZE     : integer := 16;    -- Bit size of each element
        W_IDX       : integer := 16;    -- Number of matrix elements per core
        A_IDX       : integer := 32;    -- Number of vector elements (the window)
        BLOCKS_NUM  : integer := 8;     -- Number of c blocks (2 elements each)
        CORES_NUM   : integer := 8;     -- Number of C Cores

        PC_WIDTH    : integer := 256;   -- HBM pseudo-channel data width
        W_PCS       : integer := 8;     -- weight PCs (8*256 = 2048 bits)
        A_PCS       : integer := 2;     -- activation PCs (2*256 = 512 bits)
        C_PCS       : integer := 4      -- output PCs (4*256 = 1024 bits = 64 rows)
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        -- Weights : 8 PCs
        s_axis_w_tdata  : in  std_logic_vector(W_PCS*PC_WIDTH-1 downto 0);
        s_axis_w_tvalid : in  std_logic_vector(W_PCS-1 downto 0);
        s_axis_w_tready : out std_logic_vector(W_PCS-1 downto 0);
        s_axis_w_tlast  : in  std_logic_vector(W_PCS-1 downto 0);   -- end of weight matrix

        -- Activations : 2 PCs
        s_axis_a_tdata  : in  std_logic_vector(A_PCS*PC_WIDTH-1 downto 0);
        s_axis_a_tvalid : in  std_logic_vector(A_PCS-1 downto 0);
        s_axis_a_tlast  : in  std_logic_vector(A_PCS-1 downto 0);
        s_axis_a_tready : out std_logic_vector(A_PCS-1 downto 0);

        -- Output : 4 PCs
        m_axis_c_tdata  : out std_logic_vector((BLOCKS_NUM*CORES_NUM*EL_SIZE)-1 downto 0);
        m_axis_c_tvalid : out std_logic_vector(C_PCS-1 downto 0);
        m_axis_c_tlast  : out std_logic_vector(C_PCS-1 downto 0);
        m_axis_c_tready : in std_logic_vector(C_PCS-1 downto 0)
    );
end entity dense_gemv;


architecture dense_gemv_arch of dense_gemv is

    -- Component Declarations
    component c_core_dense is
        generic(
            EL_SIZE     : integer := 16;    -- Bit size of each element
            W_IDX       : integer := 16;    -- Number of matrix elements
            A_IDX       : integer := 32;    -- Number of vector elements
            BLOCKS_NUM  : integer := 8      -- Number of c blocks (2 elements each)
        );
        port(
            clk         : in std_logic;
            resetn      : in std_logic;

            W_row       : in std_logic_vector ((W_IDX*EL_SIZE)-1 downto 0);
            A_vector    : in std_logic_vector ((A_IDX*EL_SIZE)-1 downto 0);

            win_lock    : in std_logic;     -- '0' = load a new window, '1' = advance within it

            valid_in    : in std_logic;
            tlast_in    : in std_logic;

            Cout        : out std_logic_vector ((EL_SIZE*BLOCKS_NUM)-1 downto 0);
            Cvalid      : out std_logic;
            Ctlast      : out std_logic
        );
    end component c_core_dense;

    component weights_fifo_dense is
        generic(
            PC_WIDTH   : integer := 256;    -- HBM pseudo-channel data width
            W_PCS      : integer := 8       -- weight PCs (8*256 = 2048 bits)
        );
        port(
            clk     : in std_logic;
            resetn  : in std_logic;

           -- ---- write side: AXI-Stream slave, one stream per PC (from read engine) ----
            s_axis_w_tdata    : in  std_logic_vector(W_PCS*PC_WIDTH-1 downto 0);
            s_axis_w_tvalid   : in  std_logic_vector(W_PCS-1 downto 0);
            s_axis_w_tready   : out std_logic_vector(W_PCS-1 downto 0);
            s_axis_w_tlast    : in  std_logic_vector(W_PCS-1 downto 0);   -- end of weight matrix

            -- ---- read side: one aligned word (join handshake) ----
            rd_en        : in  std_logic;                                     -- consumer pop
            ready        : out std_logic;                                     -- all PCs have a beat
            tlast_out    : out std_logic;                                     -- last weight beat popped
            W_out        : out std_logic_vector(W_PCS*PC_WIDTH-1 downto 0)    -- 2048
        );
    end component weights_fifo_dense;

    component vector_fifo_512 is
        generic(
            PC_WIDTH : integer := 256;      -- HBM pseudo-channel data width
            A_PCS    : integer := 2         -- activation PCs (2*256 = 512 bits)
        );
        port(
            clk     : in std_logic;
            resetn  : in std_logic;

             -- ---- write side: AXI-Stream slave, one stream per PC (from read engine) ----
            s_axis_a_tdata  : in  std_logic_vector(A_PCS*PC_WIDTH-1 downto 0);
            s_axis_a_tvalid : in  std_logic_vector(A_PCS-1 downto 0);
            s_axis_a_tready : out std_logic_vector(A_PCS-1 downto 0);
            s_axis_a_tlast  : in  std_logic_vector(A_PCS-1 downto 0);   -- per-PC end-of-vector

            -- ---- read side: one aligned word (join handshake) ----
            rd_en        : in  std_logic;                                    -- consumer pop
            ready        : out std_logic;                                    -- all PCs have a beat
            A_vector_out : out std_logic_vector(A_PCS*PC_WIDTH-1 downto 0);   -- 512
            tlast_out    : out std_logic
        );
    end component vector_fifo_512;

    component vector_cycle_512 is
        generic(
            PC_WIDTH : integer := 256;      -- HBM pseudo-channel data width
            A_PCS    : integer := 2         -- activation PCs (2*256 = 512 bits)
        );
        port(
            clk     : in std_logic;
            resetn  : in std_logic;

            -- ---- write side: from HBM, one strobe + rlast per pseudo-channel ----
            A_vector : in  std_logic_vector(A_PCS*PC_WIDTH-1 downto 0);
            tlast_in : in  std_logic;
            valid_in : in  std_logic;

            rd_en        : in  std_logic;
            cycle_en     : in  std_logic;
            flush        : in  std_logic;

            ready        : out std_logic;
            A_vector_out : out std_logic_vector(A_PCS*PC_WIDTH-1 downto 0);
            tlast_out    : out std_logic
        );
    end component vector_cycle_512;

    component c_fifo is
        generic(
            PC_WIDTH : integer := 256;      -- HBM pseudo-channel data width
            OUT_PCS  : integer := 4         -- output PCs (4*256 = 1024 bits)
        );
        port(
            clk     : in std_logic;
            resetn  : in std_logic;         -- active-low (tied to AXIS aresetn)

            -- ---- write side: from the C cores (one co-valid result beat) ----
            Cout      : in  std_logic_vector(OUT_PCS*PC_WIDTH-1 downto 0);
            Cvalid    : in  std_logic;
            Ctlast    : in  std_logic;
            prog_full : out std_logic;      -- to the global gate (any PC near full)

            -- ---- read side: one AXI-Stream master per output PC ----
            m_axis_c_tdata  : out std_logic_vector(OUT_PCS*PC_WIDTH-1 downto 0);
            m_axis_c_tvalid : out std_logic_vector(OUT_PCS-1 downto 0);
            m_axis_c_tlast  : out std_logic_vector(OUT_PCS-1 downto 0);
            m_axis_c_tready : in  std_logic_vector(OUT_PCS-1 downto 0)
        );
    end component c_fifo;


    -- Dense cadence: each block consumes 2 elements per cycle, so one A_IDX-element
    -- window lasts A_IDX/2 beats. This constant REPLACES the sparse 32/M freeze
    -- counter and the 2-bit Sparsity code -- there is nothing to reconfigure.
    constant FREEZE_BEATS : integer := A_IDX/2;     -- 16

    -- Internal Signals Weights FIFO (head = the FIFO join output, combinational)

    signal w_head_ready    : std_logic;   -- FIFO join: all 8 weight PCs have a beat NOW
    signal w_head_tlast    : std_logic;   -- FIFO join tlast (pop-gated -> valid on fill cycles)
    signal W_head          : std_logic_vector ((W_PCS*PC_WIDTH)-1 downto 0);

    -- Weights output STAGE (1-deep register slice, see fix note above MAIN_PROC)

    signal w_fill          : std_logic;   -- FIFO head -> stage load strobe (also pops the FIFO)
    signal w_stage_valid   : std_logic := '0';
    signal w_stage_tlast   : std_logic := '0';   -- the staged beat is the weight-matrix end
    signal rd_en_int_w     : std_logic;   -- stage consumption strobe (registered in MAIN_PROC)
    signal ready_int_w     : std_logic;   -- avail-next: a beat is GUARANTEED present next cycle
    signal tlast_int_w     : std_logic;   -- end-of-calc pulse: last weight beat CONSUMED this cycle
    signal W_row_int       : std_logic_vector ((W_PCS*PC_WIDTH)-1 downto 0);   -- stage data -> cores

    -- Internal Signals Activation FIFO (head) + output STAGE

    signal a_head_ready    : std_logic;   -- FIFO join: both activation PCs have a beat NOW
    signal a_head_tlast    : std_logic;   -- FIFO join tlast (raw head, un-gated)
    signal A_head          : std_logic_vector ((A_PCS*PC_WIDTH)-1 downto 0);

    signal a_fill          : std_logic;   -- FIFO head -> stage load strobe (also pops the FIFO)
    signal a_stage_valid   : std_logic := '0';
    signal rd_en_int_a     : std_logic;   -- stage consumption strobe (registered in MAIN_PROC)
    signal ready_int_a     : std_logic;   -- avail-next: a window is GUARANTEED present next cycle
    signal A_vector_int_a  : std_logic_vector ((A_PCS*PC_WIDTH)-1 downto 0);   -- stage data
    signal tlast_int_a     : std_logic := '0';                                 -- stage tlast (end-of-vector)
    signal a_tlast_next    : std_logic;   -- tlast of the window consumed NEXT cycle

    -- Internal Signals Activation Cycle

    signal A_vector_int_c  : std_logic_vector ((A_PCS*PC_WIDTH)-1 downto 0);
    signal tlast_int_c     : std_logic;
    signal rd_en_int_c     : std_logic;
    signal cycle_en_int    : std_logic;
    signal ready_int_c     : std_logic;

    -- Internal Signals C FIFO

    signal Cout_int        : std_logic_vector ((BLOCKS_NUM*CORES_NUM*EL_SIZE)-1 downto 0);
    signal Cvalid_int      : std_logic;
    signal Ctlast_int      : std_logic;
    signal prog_full_int   : std_logic;

    -- Internal Signals C Cores

    signal A_vector_int      : std_logic_vector ((A_PCS*PC_WIDTH)-1 downto 0);
    signal valid_in_int      : std_logic;
    signal tlast_in_int      : std_logic;
    signal win_lock          : std_logic;

    -- Control Signals

    signal counter_lock     : integer range 0 to FREEZE_BEATS-1 := 0;
    signal last_win         : std_logic := '0';   -- latched: the window just loaded carries the vector tlast
    signal settle           : std_logic := '0';   -- lap-boundary bubble (see note in MAIN_PROC)
    signal m_axis_tlast     : std_logic := '0';

    -- ---- end-of-calculation TLAST alignment (flush accounting) --------------
    -- The last weight beat is CONSUMED ~41 cycles before the results already in
    -- the pipeline DRAIN (mult 9 + add 9 + accum 20 + 3 register stages). So a
    -- naive "latch on tlast_int_w, then tag the next flush that appears" tags
    -- whichever flush happens to emerge first after the weight stream ended --
    -- the WRONG beat whenever the final lap is shorter than the drain latency.
    -- (Dense rung 3: 16-beat laps -> the marker landed on beat 0 of 2.)
    --
    -- Instead, count flushes ISSUED to the cores and flushes DRAINED from them.
    -- On the final lap the terminal branch asserts tlast_in_int and rd_en_int_w
    -- on the same cycle, so when tlast_int_w fires we already know the TOTAL
    -- number of flushes this run will ever produce -- and can tag the drained
    -- flush whose index matches. Immune to pipeline depth and lap length.
    signal flush_issued     : unsigned(15 downto 0) := (others => '0');
    signal flush_drained    : unsigned(15 downto 0) := (others => '0');
    signal flush_total      : unsigned(15 downto 0) := (others => '0');
    signal total_known      : std_logic := '0';

    -- ---- end-of-calculation TEARDOWN (re-runnability) -----------------------
    -- The engine is free-running (ap_ctrl_none): the host cannot reset it, and
    -- ap_rst_n asserts only when the FPGA is programmed. So a second calculation
    -- had to start from whatever state the first one left behind.
    --
    -- cycle_en_int is already cleared at tlast_int_w (see the replay branch), so
    -- the control DOES return to load mode. What was missing: the replay FIFO
    -- still held the previous vector, so the next load appended behind it and the
    -- head read back was stale -- run 2 computed new weights against OLD
    -- activations. On hardware that showed up as a few-percent error with the same
    -- geometry, and as garbage when the vector length changed.
    --
    -- Teardown waits until every result has DRAINED (flush_drained = flush_total)
    -- before flushing, so the end-of-calc TLAST tagging above is untouched, then
    -- pulses cyc_flush long enough for fifo_generator's synchronous reset and
    -- clears the accounting for the next run.
    signal end_pending      : std_logic := '0';
    signal cyc_flush        : std_logic := '0';
    signal flush_cnt        : integer range 0 to 15 := 0;

begin

    -- Tag ONLY the final flush: the one draining now is number flush_drained+1
    -- (flush_drained counts those already gone), and flush_total is the run's
    -- total, known once the last weight beat has been consumed.
    m_axis_tlast <= '1' when (total_known = '1' AND Ctlast_int = '1' AND
                              (flush_drained + 1) = flush_total)
                    else '0';

    -- Activation window mux: COMBINATIONAL so it tracks the same beat cycle as the
    -- weights bus (W_row_int). A registered mux lags one cycle and mispairs windows.
    A_vector_int <= A_vector_int_c when cycle_en_int = '1' else A_vector_int_a;

    -- ------------------------------------------------------------------------
    -- Ingress output STAGES (FWFT drain-hazard fix).
    --
    -- MAIN_PROC decides at edge N (sampling 'ready') but the beat is consumed
    -- during cycle N (registered rd_en/valid). The FIFO join's raw all_valid
    -- stays high through the cycle its LAST beat is popped, so under input
    -- bubbles a decision could commit a consumption for a cycle where the FIFO
    -- is already empty -> the cores would re-accumulate the stale head (the
    -- stress-test corruption). A 1-deep register stage in front of the consumer
    -- closes the skew: it holds the beat being consumed, and
    --     ready = head_ready OR (stage_valid AND NOT rd_en)
    -- equals stage_valid of the NEXT cycle exactly, so every commit is
    -- guaranteed a real beat. Full throughput is preserved: with data present
    -- the stage refills on every consumption cycle (fill = pop-through).
    --
    --   * w_fill/a_fill pop the FIFO join into the stage whenever the stage is
    --     empty or being consumed this cycle (autonomous, like a FIFO extension).
    --   * rd_en_int_w/rd_en_int_a are the STAGE consumption strobes; the
    --     replay-buffer path (ready_int_c/tlast_int_c) is unchanged -- during
    --     replay wr_en = rd_en keeps its occupancy constant, it cannot drain.
    --   * Decision-time metadata (last_win) uses a_tlast_next = metadata of the
    --     beat present next cycle; consumption-time metadata (tlast_int_w) uses
    --     the stage itself.
    -- ------------------------------------------------------------------------
    w_fill <= w_head_ready AND ((NOT w_stage_valid) OR rd_en_int_w);
    a_fill <= a_head_ready AND ((NOT a_stage_valid) OR rd_en_int_a);

    ready_int_w <= w_head_ready OR (w_stage_valid AND (NOT rd_en_int_w));
    ready_int_a <= a_head_ready OR (a_stage_valid AND (NOT rd_en_int_a));

    -- Metadata of the window that will be in the stage NEXT cycle: the staged
    -- beat if it is kept, else the FIFO head (the refill). Only meaningful when
    -- ready_int_a is '1' -- exactly when MAIN_PROC may commit.
    a_tlast_next  <= tlast_int_a when (a_stage_valid = '1' AND rd_en_int_a = '0') else a_head_tlast;

    -- End-of-calc pulse: the last weight beat is consumed (from the stage) this
    -- cycle. rd_en is only ever asserted with the stage guaranteed valid.
    tlast_int_w <= rd_en_int_w AND w_stage_tlast;

    STAGE_PROC : process(clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then
                w_stage_valid <= '0';
                w_stage_tlast <= '0';
                a_stage_valid <= '0';
                tlast_int_a   <= '0';
            else
                if w_fill = '1' then
                    W_row_int     <= W_head;
                    w_stage_tlast <= w_head_tlast;   -- pop-gated join tlast = head tlast on fill cycles
                    w_stage_valid <= '1';
                elsif rd_en_int_w = '1' then
                    w_stage_valid <= '0';
                end if;

                if a_fill = '1' then
                    A_vector_int_a <= A_head;
                    tlast_int_a    <= a_head_tlast;
                    a_stage_valid  <= '1';
                elsif rd_en_int_a = '1' then
                    a_stage_valid  <= '0';
                end if;
            end if;
        end if;
    end process STAGE_PROC;

    MAIN_PROC : process(clk)
    begin

        if rising_edge(clk) then

            if resetn = '0' then

                rd_en_int_w     <= '0';
                rd_en_int_a     <= '0';
                rd_en_int_c     <= '0';
                cycle_en_int    <= '0';
                valid_in_int    <= '0';
                tlast_in_int    <= '0';
                win_lock        <= '0';

                counter_lock    <= 0;
                last_win        <= '0';
                settle          <= '0';

                flush_issued    <= (others => '0');
                flush_drained   <= (others => '0');
                flush_total     <= (others => '0');
                total_known     <= '0';

                end_pending     <= '0';
                cyc_flush       <= '0';
                flush_cnt       <= 0;

            else

                -- ---- end-of-calculation teardown (see note at declarations) --
                if tlast_int_w = '1' then
                    end_pending <= '1';
                end if;

                if cyc_flush = '1' then
                    if flush_cnt > 1 then
                        flush_cnt <= flush_cnt - 1;
                    else
                        -- flush window over: the replay FIFO is empty and the
                        -- accounting is cleared, so the next calculation starts
                        -- exactly as it would after a power-on reset.
                        cyc_flush     <= '0';
                        flush_cnt     <= 0;
                        end_pending   <= '0';
                        flush_issued  <= (others => '0');
                        flush_drained <= (others => '0');
                        flush_total   <= (others => '0');
                        total_known   <= '0';
                        last_win      <= '0';
                        settle        <= '0';
                    end if;
                elsif end_pending = '1' AND total_known = '1' AND
                      flush_drained = flush_total then
                    -- every result is out; safe to empty the replay buffer
                    cyc_flush <= '1';
                    flush_cnt <= 8;              -- >= 3 cycles of srst, with margin
                end if;

                -- ---- flush accounting (see the note at the declarations) ----
                if tlast_in_int = '1' then
                    flush_issued <= flush_issued + 1;
                end if;

                if Ctlast_int = '1' then
                    flush_drained <= flush_drained + 1;
                end if;

                if tlast_int_w = '1' then
                    -- Total = flushes issued before this cycle, plus the final
                    -- lap's own flush when it is issued on this very cycle (the
                    -- normal case: the terminal branch drives both together).
                    if tlast_in_int = '1' then
                        flush_total <= flush_issued + 1;
                    else
                        flush_total <= flush_issued;
                    end if;
                    total_known <= '1';
                end if;

                if settle = '1' then
                    -- One-cycle lap-boundary bubble, carried over from the verified
                    -- sparse control. In sparse it was mandatory (the next group's
                    -- Sparsity had to be sampled from a refilled stage); in dense
                    -- there is no per-group mode to resample, so it is a pure
                    -- 1-cycle-per-lap conservatism (16 cycles out of 8192 on the
                    -- 1024x1024 case = 0.2%). Kept for behavioural parity with the
                    -- verified control; removable once the multi-lap rung passes.
                    settle       <= '0';
                    rd_en_int_w  <= '0';
                    rd_en_int_a  <= '0';
                    rd_en_int_c  <= '0';
                    valid_in_int <= '0';
                    tlast_in_int <= '0';

                elsif cycle_en_int = '0' then      -- Start of Calculations with new A vector

                    rd_en_int_c <= '0';    -- Disable reading from the cycle fifo

                    if counter_lock = 0 then    -- New read from Activations vector

                        -- Do not start a new window once the weight matrix has ended.
                        if ready_int_w = '1' AND ready_int_a = '1' AND prog_full_int = '0'
                           AND tlast_int_w = '0' then  -- inputs ready + output not near-full + not past end

                            rd_en_int_w <= '1';
                            rd_en_int_a <= '1';
                            win_lock <= '0';
                            valid_in_int <= '1';
                            last_win <= a_tlast_next;   -- latch whether the window being loaded is the last

                            counter_lock <= FREEZE_BEATS-1;   -- dense: fixed 16-beat window
                            tlast_in_int <= '0';

                        else

                            rd_en_int_w <= '0';
                            rd_en_int_a <= '0';
                            valid_in_int <= '0';
                            tlast_in_int <= '0';
                            win_lock <= '0';   -- load boundary: nothing to advance while stalled

                        end if;

                    else        -- Read only from weights and advance within the frozen window

                        if ready_int_w = '1' AND prog_full_int = '0' then

                            if last_win = '1' AND counter_lock = 1 then

                                tlast_in_int <= '1';
                                cycle_en_int <= '1';
                                counter_lock <= 0;
                                rd_en_int_a <= '0';
                                valid_in_int <= '1';
                                rd_en_int_w <= '1';
                                win_lock <= '1';
                                settle       <= '1';   -- lap boundary -> bubble before next group's load

                            else

                                rd_en_int_w <= '1';
                                rd_en_int_a <= '0';
                                valid_in_int <= '1';
                                counter_lock <= counter_lock - 1;
                                win_lock <= '1';
                                tlast_in_int <= '0';
                            end if;

                        else

                            rd_en_int_w <= '0';
                            rd_en_int_a <= '0';
                            valid_in_int <= '0';
                            win_lock <= '1';
                            tlast_in_int <= '0';

                        end if;
                    end if;

                else    -- Cycle Enable = 1 => Activations vector replayed from the cycle fifo

                    rd_en_int_a <= '0';    -- Disable reading from the activation fifo

                    if tlast_int_w = '1' then
                        cycle_en_int <= '0';
                    end if;

                    if counter_lock = 0 then

                        -- Do not start a new replay lap once the weight matrix has ended.
                        if ready_int_w = '1' AND ready_int_c = '1' AND prog_full_int = '0'
                           AND tlast_int_w = '0' then  -- inputs ready + output not near-full + not past end

                            rd_en_int_w <= '1';
                            rd_en_int_c <= '1';
                            win_lock <= '0';
                            valid_in_int <= '1';
                            last_win <= tlast_int_c;   -- latch whether the window being loaded is the last
                                                       -- (replay head is un-staged: rd_en_int_c was 0 last
                                                       -- cycle, so the head IS the next window)

                            counter_lock <= FREEZE_BEATS-1;   -- dense: fixed 16-beat window
                            tlast_in_int <= '0';

                        else

                            rd_en_int_w <= '0';
                            rd_en_int_c <= '0';
                            valid_in_int <= '0';
                            tlast_in_int <= '0';
                            win_lock <= '0';   -- load boundary: nothing to advance while stalled

                        end if;

                    else

                        if ready_int_w = '1' AND prog_full_int = '0' then

                            if last_win = '1' AND counter_lock = 1 then

                                tlast_in_int <= '1';
                                counter_lock <= 0;
                                rd_en_int_c <= '0';
                                valid_in_int <= '1';
                                rd_en_int_w <= '1';
                                win_lock <= '1';
                                settle       <= '1';   -- lap boundary -> bubble before next group's load
                            else

                                rd_en_int_w <= '1';
                                rd_en_int_c <= '0';
                                valid_in_int <= '1';
                                counter_lock <= counter_lock - 1;
                                win_lock <= '1';
                                tlast_in_int <= '0';
                            end if;

                        else

                            rd_en_int_w <= '0';
                            rd_en_int_c <= '0';
                            valid_in_int <= '0';
                            tlast_in_int <= '0';
                            win_lock <= '1';

                        end if;
                    end if;
                end if;
            end if;
        end if;
    end process MAIN_PROC;



    WEIGHTS_FIFO_INST : weights_fifo_dense
        generic map(
            PC_WIDTH   => PC_WIDTH,
            W_PCS      => W_PCS
        )
        port map(
            clk         => clk,
            resetn      => resetn,

            s_axis_w_tdata  => s_axis_w_tdata,
            s_axis_w_tvalid => s_axis_w_tvalid,
            s_axis_w_tready => s_axis_w_tready,
            s_axis_w_tlast  => s_axis_w_tlast,

            rd_en           => w_fill,          -- stage refill pops the join
            ready           => w_head_ready,
            tlast_out       => w_head_tlast,
            W_out           => W_head
        );

    ACTIVATION_FIFO_INST : vector_fifo_512
        generic map(
            PC_WIDTH   => PC_WIDTH,
            A_PCS      => A_PCS
        )
        port map(
            clk         => clk,
            resetn      => resetn,

            s_axis_a_tdata  => s_axis_a_tdata,
            s_axis_a_tvalid => s_axis_a_tvalid,
            s_axis_a_tready => s_axis_a_tready,
            s_axis_a_tlast  => s_axis_a_tlast,

            rd_en           => a_fill,          -- stage refill pops the join
            ready           => a_head_ready,
            A_vector_out    => A_head,
            tlast_out       => a_head_tlast
        );

    ACTIVATION_CYCLE_INST : vector_cycle_512
        generic map(
            PC_WIDTH   => PC_WIDTH,
            A_PCS      => A_PCS
        )
        port map(
            clk         => clk,
            resetn      => resetn,

            A_vector    => A_vector_int_a,
            tlast_in    => tlast_int_a,
            valid_in    => rd_en_int_a,

            rd_en       => rd_en_int_c,
            cycle_en    => cycle_en_int,
            flush       => cyc_flush,

            ready           => ready_int_c,
            A_vector_out    => A_vector_int_c,
            tlast_out       => tlast_int_c
        );

    C_FIFO_INST : c_fifo
        generic map(
            PC_WIDTH   => PC_WIDTH,
            OUT_PCS    => C_PCS
        )
        port map(
            clk         => clk,
            resetn      => resetn,

            Cout        => Cout_int,
            Cvalid      => Cvalid_int,
            Ctlast      => m_axis_tlast,
            prog_full   => prog_full_int,

            m_axis_c_tdata  => m_axis_c_tdata,
            m_axis_c_tvalid => m_axis_c_tvalid,
            m_axis_c_tlast  => m_axis_c_tlast,
            m_axis_c_tready => m_axis_c_tready
        );

    CORES_GEN : for i in 0 to CORES_NUM-1 generate

        C_CORE_INST_FIRST : if i = 0 generate

            C_CORE_FIRST : c_core_dense
                generic map(
                    EL_SIZE     => EL_SIZE,
                    W_IDX       => W_IDX,
                    A_IDX       => A_IDX,
                    BLOCKS_NUM  => BLOCKS_NUM
                )
                port map(
                    clk         => clk,
                    resetn      => resetn,

                    W_row       => W_row_int(((W_IDX*EL_SIZE)*(i+1))-1 downto (W_IDX*EL_SIZE)*i),
                    A_vector    => A_vector_int,

                    win_lock    => win_lock,

                    valid_in    => valid_in_int,
                    tlast_in    => tlast_in_int,

                    Cout        => Cout_int((i+1)*EL_SIZE*BLOCKS_NUM-1 downto i*EL_SIZE*BLOCKS_NUM),
                    Cvalid      => Cvalid_int,
                    Ctlast      => Ctlast_int
                );
        end generate C_CORE_INST_FIRST;

        C_CORE_INST_REST : if i /=0 generate

            C_CORE_REST : c_core_dense
                generic map(
                    EL_SIZE     => EL_SIZE,
                    W_IDX       => W_IDX,
                    A_IDX       => A_IDX,
                    BLOCKS_NUM  => BLOCKS_NUM
                )
                port map(
                    clk         => clk,
                    resetn      => resetn,

                    W_row       => W_row_int(((W_IDX*EL_SIZE)*(i+1))-1 downto (W_IDX*EL_SIZE)*i),
                    A_vector    => A_vector_int,

                    win_lock    => win_lock,

                    valid_in    => valid_in_int,
                    tlast_in    => tlast_in_int,

                    Cout        => Cout_int((i+1)*EL_SIZE*BLOCKS_NUM-1 downto i*EL_SIZE*BLOCKS_NUM),
                    Cvalid      => open,
                    Ctlast      => open
                );
        end generate C_CORE_INST_REST;
    end generate CORES_GEN;


end architecture dense_gemv_arch;
