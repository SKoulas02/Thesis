library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
-- 
-- Description:
-- This Module is used to calculate the output of a 2:N GEMV calculation.
-- Input is elements for Weight Matrix, Activation Vector, the Indices and Sparsity bits needed for the calculations.
-- It is made up of FIFOs for HBM latency synchronization and parallel C Cores processing the operations.
-- The module can be reused for different GEMV operations and is fully pipelined.
-- ----------------------------------------------------------------------------

entity two2N is
    generic(
        EL_SIZE     : integer := 16;    -- Bit size of each element
        W_IDX       : integer := 16;     -- Number of matrix elements
        A_IDX       : integer := 32;    -- Number of vector elements
        IND_NUM     : integer := 10;     -- Number of Indices Bits
        BLOCKS_NUM  : integer := 8;     -- Number of c blocks (2 elements each)
        CORES_NUM   : integer := 8;     -- Number of C Cores

        PC_WIDTH    : integer := 256;    -- HBM pseudo-channel data width
        W_PCS       : integer := 8;      -- weight PCs (8*256 = 2048 bits)
        A_PCS       : integer := 2;      -- activation PCs (2*256 = 512 bits)
        IND_PCS     : integer := 3;      -- index  PCs (3*256 =  768 bits, 640 used)
        C_PCS       : integer := 4;      -- output PCs (4*256 = 1024 bits = 2:32 max)
        IND_BITS    : integer := 640      -- useful index bits (remainder is padding)
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        -- Weights : 8 PCs
        s_axis_w_tdata  : in  std_logic_vector(W_PCS*PC_WIDTH-1 downto 0);
        s_axis_w_tvalid : in  std_logic_vector(W_PCS-1 downto 0);
        s_axis_w_tready : out std_logic_vector(W_PCS-1 downto 0);
        s_axis_w_tlast  : in  std_logic_vector(W_PCS-1 downto 0);   -- end of weight matrix

        -- Indices : 3 PCs
        s_axis_ind_tdata  : in  std_logic_vector(IND_PCS*PC_WIDTH-1 downto 0);
        s_axis_ind_tvalid : in  std_logic_vector(IND_PCS-1 downto 0);
        s_axis_ind_tready : out std_logic_vector(IND_PCS-1 downto 0);

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
end entity two2N;


architecture two2N_arch of two2N is

    -- Component Declarations
    component c_core is
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
    end component c_core;

    component weights_fifo_2k is
        generic(
            PC_WIDTH   : integer := 256;    -- HBM pseudo-channel data width
            W_PCS      : integer := 8;      -- weight PCs (8*256 = 2048 bits)
            IND_PCS    : integer := 3;      -- index  PCs (3*256 =  768 bits, 640 used)
            IND_BITS   : integer := 640     -- useful index bits (remainder is padding)
        );
        port(
            clk     : in std_logic;
            resetn  : in std_logic;

           -- ---- write side: AXI-Stream slave, one stream per PC (from read engine) ----
            s_axis_w_tdata    : in  std_logic_vector(W_PCS*PC_WIDTH-1 downto 0);
            s_axis_w_tvalid   : in  std_logic_vector(W_PCS-1 downto 0);
            s_axis_w_tready   : out std_logic_vector(W_PCS-1 downto 0);
            s_axis_w_tlast    : in  std_logic_vector(W_PCS-1 downto 0);   -- end of weight matrix

            s_axis_ind_tdata  : in  std_logic_vector(IND_PCS*PC_WIDTH-1 downto 0);
            s_axis_ind_tvalid : in  std_logic_vector(IND_PCS-1 downto 0);
            s_axis_ind_tready : out std_logic_vector(IND_PCS-1 downto 0);

            -- ---- read side: one aligned word (join handshake) ----
            rd_en        : in  std_logic;                                    -- consumer pop
            ready        : out std_logic;                                    -- all PCs have a beat
            tlast_out    : out std_logic;                                    -- last weight beat popped
            W_out        : out std_logic_vector(W_PCS*PC_WIDTH-1 downto 0);   -- 2048
            indices_out  : out std_logic_vector(IND_BITS-1 downto 0);        -- 640
            Sparsity_out : out std_logic_vector(1 downto 0)
        );
    end component weights_fifo_2k;

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
            OUT_PCS  : integer := 4         -- output PCs (4*256 = 1024 bits = 2:32 max)
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


    -- Internal Signals Weights FIFO (head = the FIFO join output, combinational)

    signal w_head_ready    : std_logic;   -- FIFO join: all 11 PCs have a beat NOW
    signal w_head_tlast    : std_logic;   -- FIFO join tlast (pop-gated -> valid on fill cycles)
    signal W_head          : std_logic_vector ((W_PCS*PC_WIDTH)-1 downto 0);
    signal Ind_head        : std_logic_vector (IND_BITS-1 downto 0);
    signal Sparsity        : std_logic_vector (1 downto 0);   -- FIFO head sparsity (combinational)

    -- Weights/indices output STAGE (1-deep register slice, see fix note above MAIN_PROC)

    signal w_fill          : std_logic;   -- FIFO head -> stage load strobe (also pops the FIFO)
    signal w_stage_valid   : std_logic := '0';
    signal w_stage_tlast   : std_logic := '0';   -- the staged beat is the weight-matrix end
    signal spar_stage      : std_logic_vector (1 downto 0) := (others => '0');
    signal rd_en_int_w     : std_logic;   -- stage consumption strobe (registered in MAIN_PROC)
    signal ready_int_w     : std_logic;   -- avail-next: a beat is GUARANTEED present next cycle
    signal tlast_int_w     : std_logic;   -- end-of-calc pulse: last weight beat CONSUMED this cycle
    signal W_row_int       : std_logic_vector ((W_PCS*PC_WIDTH)-1 downto 0);   -- stage data -> cores
    signal Indices_int     : std_logic_vector (IND_BITS-1 downto 0);           -- stage data -> cores
    signal sparsity_next   : std_logic_vector (1 downto 0);   -- sparsity of the beat consumed NEXT cycle

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
    signal sparsity_lock     : std_logic;

    -- Control Signals

    signal counter_lock     : integer range 0 to 7 := 0;
    signal last_win         : std_logic := '0';   -- latched: the window just loaded carries the vector tlast
    signal settle           : std_logic := '0';   -- lap-boundary bubble: hold one cycle so the weights-FIFO
                                                   -- head advances to the next group's first beat before its
                                                   -- Sparsity is sampled (needed for runtime 2:M reconfiguration)
    signal m_axis_tlast     : std_logic := '0';

    signal flush_2to32      : std_logic;          -- combinational 2:32 vector-end flush (no-freeze path)
    signal tlast_in_core    : std_logic;          -- cores' tlast_in = registered terminals OR 2:32 flush

    -- ---- end-of-calculation TLAST alignment (flush accounting) --------------
    -- The last weight beat is CONSUMED ~41 cycles before the results already in
    -- the pipeline DRAIN (mult 9 + add 9 + accum 20 + 3 register stages). The
    -- previous logic latched 'tlast_end' on tlast_int_w and tagged the next
    -- flush to appear -- the WRONG beat whenever the final lap is shorter than
    -- the drain latency. The 1024x1024 run ends with a 2:32 group at 32
    -- beats/lap, so it tagged flush 14 of 16. (It only looked correct under the
    -- stress testbench, whose input bubbles stretch a lap to ~80 cycles and let
    -- the pipeline empty first -- i.e. the stress run MASKED the bug, and
    -- full-rate back-to-back streaming is the case that exposes it.)
    --
    -- Instead, count flushes ISSUED to the cores and flushes DRAINED from them.
    -- The final flush trigger always coincides with tlast_int_w -- in the freeze
    -- modes the terminal branch drives tlast_in_int and rd_en_int_w together,
    -- and at 2:32 the flush_2to32 cycle is the very cycle its beat is consumed --
    -- so when tlast_int_w fires the run's TOTAL flush count is already known and
    -- we can tag the drained flush whose index matches. Immune to pipeline
    -- depth, lap length and sparsity mode.
    --
    -- NOTE: counts tlast_in_core (= tlast_in_int OR flush_2to32), so BOTH flush
    -- paths are covered. They are mutually exclusive, so it stays a clean
    -- one-pulse-per-flush strobe.
    signal flush_issued     : unsigned(15 downto 0) := (others => '0');
    signal flush_drained    : unsigned(15 downto 0) := (others => '0');
    signal flush_total      : unsigned(15 downto 0) := (others => '0');
    signal total_known      : std_logic := '0';

    -- ---- end-of-calculation TEARDOWN (re-runnability) -----------------------
    -- Same fix as top_module_dense.vhd, applied 2026-08-24 for the same reason.
    -- The engine is free-running (ap_ctrl_none): the host cannot reset it, and
    -- ap_rst_n asserts only when the FPGA is programmed, so a second calculation
    -- inherits whatever the first left behind. TWO causes, which hid each other:
    --
    --  1. cycle_en_int was never cleared. The replay branch does carry
    --         if tlast_int_w = '1' then cycle_en_int <= '0';
    --     but it sits under
    --         if settle='1' then <bubble> elsif cycle_en_int='0' then <load>
    --         else <replay>
    --     and the final lap's terminal sets settle <= '1' the cycle BEFORE
    --     tlast_int_w rises -- so the bubble branch (which explicitly HOLDS
    --     cycle_en) runs instead and the clear is skipped exactly when needed.
    --  2. The replay FIFO was never emptied, so replay mode found the previous
    --     vector and produced new weights x OLD activations.
    --
    -- Alone, either is obvious: stuck-in-replay with an empty FIFO deadlocks,
    -- a stale FIFO with a working mode reset is harmless. Together they give
    -- plausible-looking wrong numbers, which is why hardware found this and no
    -- testbench did (they all run one calculation from reset).
    --
    -- Teardown waits until every result has DRAINED (flush_drained = flush_total)
    -- so the end-of-calc TLAST tagging above is untouched, then pulses cyc_flush
    -- long enough for fifo_generator's synchronous reset and clears the state.
    -- It lives OUTSIDE the settle/load/replay chain, so it always executes.
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
    -- weights bus (W_row_int). A registered mux lags one cycle and mispairs windows
    -- when 2:32 (no freeze) reads a new window every cycle.
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
    --   * rd_en_int_w/rd_en_int_a are now the STAGE consumption strobes; the
    --     replay-buffer path (ready_int_c/tlast_int_c) is unchanged -- during
    --     replay wr_en = rd_en keeps its occupancy constant, it cannot drain.
    --   * Decision-time metadata (counter_lock case, last_win) uses the
    --     *_next view = metadata of the beat present next cycle; consumption-
    --     time metadata (flush_2to32, tlast_int_w) uses the stage itself.
    -- ------------------------------------------------------------------------
    w_fill <= w_head_ready AND ((NOT w_stage_valid) OR rd_en_int_w);
    a_fill <= a_head_ready AND ((NOT a_stage_valid) OR rd_en_int_a);

    ready_int_w <= w_head_ready OR (w_stage_valid AND (NOT rd_en_int_w));
    ready_int_a <= a_head_ready OR (a_stage_valid AND (NOT rd_en_int_a));

    -- Metadata of the beat that will be in the stage NEXT cycle: the staged beat
    -- if it is kept, else the FIFO head (the refill). Only meaningful when the
    -- matching ready is '1' -- exactly when MAIN_PROC may commit.
    sparsity_next <= spar_stage  when (w_stage_valid = '1' AND rd_en_int_w = '0') else Sparsity;
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
                spar_stage    <= (others => '0');
                a_stage_valid <= '0';
                tlast_int_a   <= '0';
            else
                if w_fill = '1' then
                    W_row_int     <= W_head;
                    Indices_int   <= Ind_head;
                    spar_stage    <= Sparsity;
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

    -- 2:32 combinational flush: with no freeze, the vector-end window is processed the
    -- same cycle its beat is consumed, so its tlast must reach the cores co-timed.
    -- (The registered tlast_in_int decision is one cycle late for back-to-back reads.)
    -- Consumption-cycle metadata: the STAGE holds the beat being consumed (spar_stage /
    -- tlast_int_a); the replay head (tlast_int_c) is popped this cycle -- also consumed.
    -- valid_in_int guards idle/settle cycles and makes the FIRST window commit (NWIN=1).
    flush_2to32 <= '1' when (spar_stage = "11" AND valid_in_int = '1' AND
                             ((cycle_en_int = '0' AND tlast_int_a = '1') OR
                              (cycle_en_int = '1' AND tlast_int_c = '1')))
                   else '0';

    tlast_in_core <= tlast_in_int OR flush_2to32;

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
                sparsity_lock   <= '0';

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
                        -- Flush window over: replay FIFO empty, accounting clear,
                        -- so the next calculation starts exactly as it would
                        -- after a power-on reset.
                        --
                        -- cycle_en_int MUST be cleared HERE, not only in the
                        -- replay branch -- see the note at the declarations for
                        -- why that branch's clear is skipped.
                        cycle_en_int  <= '0';
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
                if tlast_in_core = '1' then
                    flush_issued <= flush_issued + 1;
                end if;

                if Ctlast_int = '1' then
                    flush_drained <= flush_drained + 1;
                end if;

                if tlast_int_w = '1' then
                    -- Total = flushes issued before this cycle, plus the final
                    -- lap's own flush when it is triggered on this very cycle
                    -- (the normal case, on both the freeze and 2:32 paths).
                    if tlast_in_core = '1' then
                        flush_total <= flush_issued + 1;
                    else
                        flush_total <= flush_issued;
                    end if;
                    total_known <= '1';
                end if;

                if settle = '1' then
                    -- One-cycle lap-boundary bubble. The previous lap's terminal consumed its
                    -- last weight beat this-1 cycle; hold now so the weights STAGE refills with
                    -- the next group's FIRST beat before its Sparsity is sampled. Without this
                    -- a group whose sparsity differs from the previous one loads the wrong freeze
                    -- length (runtime 2:M reconfiguration). cycle_en / counter_lock hold.
                    settle       <= '0';
                    rd_en_int_w  <= '0';
                    rd_en_int_a  <= '0';
                    rd_en_int_c  <= '0';
                    valid_in_int <= '0';
                    tlast_in_int <= '0';

                elsif cycle_en_int = '0' then      -- Start of Calculations with new A vector

                    rd_en_int_c <= '0';    -- Disable reading from the cycle fifo

                    if counter_lock = 0 then    -- New read from Activations vector
                    
                        -- 2:32 vector-end: the window flushing THIS cycle (flush_2to32) is the
                        -- lap's last, and its read is already in flight (back-to-back). Stop
                        -- issuing and switch to replay. Its pop advances the FIFO heads on this
                        -- same edge, so the next decision samples the following group's first
                        -- beat directly -- no settle needed on this path.
                        if flush_2to32 = '1' then

                            rd_en_int_w   <= '0';
                            rd_en_int_a   <= '0';
                            valid_in_int  <= '0';
                            tlast_in_int  <= '0';
                            sparsity_lock <= '0';
                            cycle_en_int  <= '1';

                        -- Do not start a new window once the weight matrix has ended
                        -- (tlast_int_w). Exception: 2:32 has no freeze, so the load cycle
                        -- IS where the final beat is processed.
                        -- ... and never during the end-of-calculation flush: the replay
                        -- FIFO is held in reset there, so a load would consume an
                        -- activation beat from the ingress stage and lose it.
                        elsif ready_int_w = '1' AND ready_int_a = '1' AND prog_full_int = '0'
                           AND cyc_flush = '0'
                           AND (sparsity_next = "11" OR tlast_int_w = '0') then

                            rd_en_int_w <= '1';
                            rd_en_int_a <= '1';
                            sparsity_lock <= '0';
                            valid_in_int <= '1';
                            last_win <= a_tlast_next;   -- latch whether the window being loaded is the last

                            case sparsity_next is
                                when "00" =>    -- 2:4 Sparsity => 8 Cycles Freeze
                                    counter_lock <= 7;
                                when "01" =>    -- 2:8 Sparsity => 4 Cycles Freeze
                                    counter_lock <= 3;
                                when "10" =>    -- 2:16 Sparsity => 2 Cycles Freeze
                                    counter_lock <= 1;
                                when "11" =>    -- 2:32 Sparsity => No Freeze
                                    counter_lock <= 0;
                                when others =>
                                    null;
                            end case;

                            -- 2:32 flush + lap transition are handled by the combinational
                            -- flush_2to32 / stop branch above; the registered tlast stays low here.
                            tlast_in_int <= '0';

                        else

                            rd_en_int_w <= '0';
                            rd_en_int_a <= '0';
                            valid_in_int <= '0';
                            tlast_in_int <= '0';
                            sparsity_lock <= '0';   -- load boundary: nothing to freeze while stalled

                        end if;

                    else        -- Read only from weights and freeze activations vector

                        if ready_int_w = '1' AND prog_full_int = '0' then

                            if last_win = '1' AND counter_lock = 1 then

                                tlast_in_int <= '1';
                                cycle_en_int <= '1';
                                counter_lock <= 0;
                                rd_en_int_a <= '0';
                                valid_in_int <= '1';
                                rd_en_int_w <= '1';
                                sparsity_lock <= '1';
                                settle       <= '1';   -- lap boundary -> bubble before next group's load

                            else

                                rd_en_int_w <= '1';
                                rd_en_int_a <= '0';
                                valid_in_int <= '1';
                                counter_lock <= counter_lock - 1;
                                sparsity_lock <= '1';
                                tlast_in_int <= '0';
                            end if;

                            

                        else

                            rd_en_int_w <= '0';
                            rd_en_int_a <= '0';
                            valid_in_int <= '0';
                            sparsity_lock <= '1';
                            tlast_in_int <= '0';


                        end if;
                    end if;

                else    -- Cycle Enable = 1 => Activations vector changed fifo 

                    rd_en_int_a <= '0';    -- Disable reading from the activation fifo

                    if tlast_int_w = '1' then
                        cycle_en_int <= '0';
                    end if;

                    if counter_lock = 0 then
                    
                        -- 2:32 vector-end (replay): stop issuing on the flush cycle; the
                        -- replay->load transition is driven by the weight tlast, so no
                        -- cycle_en change here. Heads advance on this cycle's pop.
                        if flush_2to32 = '1' then

                            rd_en_int_w   <= '0';
                            rd_en_int_c   <= '0';
                            valid_in_int  <= '0';
                            tlast_in_int  <= '0';
                            sparsity_lock <= '0';

                        -- Do not start a new replay lap once the weight matrix has ended
                        -- (tlast_int_w). Exception: 2:32 processes its final beat here.
                        elsif ready_int_w = '1' AND ready_int_c = '1' AND prog_full_int = '0'
                           AND (sparsity_next = "11" OR tlast_int_w = '0') then  -- inputs ready + output not near-full + not past end

                            rd_en_int_w <= '1';
                            rd_en_int_c <= '1';
                            sparsity_lock <= '0';
                            valid_in_int <= '1';
                            last_win <= tlast_int_c;   -- latch whether the window being loaded is the last
                                                       -- (replay head is un-staged: in freeze modes rd_en_int_c
                                                       -- was 0 last cycle so the head IS the next window; at
                                                       -- 2:32 last_win is unused -- flush_2to32 covers the lap end)

                            case sparsity_next is
                                when "00" =>    -- 2:4 Sparsity => 8 Cycles Freeze
                                    counter_lock <= 7;
                                when "01" =>    -- 2:8 Sparsity => 4 Cycles Freeze
                                    counter_lock <= 3;
                                when "10" =>    -- 2:16 Sparsity => 2 Cycles Freeze
                                    counter_lock <= 1;
                                when "11" =>    -- 2:32 Sparsity => No Freeze
                                    counter_lock <= 0;
                                when others =>
                                    null;
                            end case;

                            -- 2:32 flush is handled by the combinational flush_2to32 / stop
                            -- branch above; the registered tlast stays low here.
                            tlast_in_int <= '0';

                        else

                            rd_en_int_w <= '0';
                            rd_en_int_c <= '0';
                            valid_in_int <= '0';
                            tlast_in_int <= '0';
                            sparsity_lock <= '0';   -- load boundary: nothing to freeze while stalled

                        end if;

                    else

                        if ready_int_w = '1' AND prog_full_int = '0' then

                            if last_win = '1' AND counter_lock = 1 then

                                tlast_in_int <= '1';
                                counter_lock <= 0;
                                rd_en_int_c <= '0';
                                valid_in_int <= '1';
                                rd_en_int_w <= '1';
                                sparsity_lock <= '1';
                                settle       <= '1';   -- lap boundary -> bubble before next group's load
                            else

                                rd_en_int_w <= '1';
                                rd_en_int_c <= '0';
                                valid_in_int <= '1';
                                counter_lock <= counter_lock - 1;
                                sparsity_lock <= '1';
                                tlast_in_int <= '0';
                            end if;
                        
                        else

                            rd_en_int_w <= '0';
                            rd_en_int_c <= '0';
                            valid_in_int <= '0';
                            tlast_in_int <= '0';
                            sparsity_lock <= '1';
                        
                        end if;
                    end if;
                end if;
            end if;
        end if;
    end process MAIN_PROC;



    WEIGHTS_FIFO_INST : weights_fifo_2k
        generic map(
            PC_WIDTH   => PC_WIDTH,
            W_PCS      => W_PCS,
            IND_PCS    => IND_PCS,
            IND_BITS   => IND_BITS
        )
        port map(
            clk         => clk,
            resetn      => resetn,

            s_axis_w_tdata  => s_axis_w_tdata,
            s_axis_w_tvalid => s_axis_w_tvalid,
            s_axis_w_tready => s_axis_w_tready,
            s_axis_w_tlast  => s_axis_w_tlast,

            s_axis_ind_tdata  => s_axis_ind_tdata,
            s_axis_ind_tvalid => s_axis_ind_tvalid,
            s_axis_ind_tready => s_axis_ind_tready,

            rd_en           => w_fill,          -- stage refill pops the join
            ready           => w_head_ready,
            tlast_out       => w_head_tlast,
            W_out           => W_head,
            indices_out     => Ind_head,
            Sparsity_out    => Sparsity
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

            C_CORE_FIRST : c_core
                generic map(
                    EL_SIZE     => EL_SIZE,
                    W_IDX       => W_IDX,
                    A_IDX       => A_IDX,
                    BLOCKS_NUM  => BLOCKS_NUM,
                    IND_NUM     => IND_NUM
                )
                port map(
                    clk         => clk,
                    resetn      => resetn,

                    W_row       => W_row_int(((W_IDX*EL_SIZE)*(i+1))-1 downto (W_IDX*EL_SIZE)*i),
                    Indices     => Indices_int(((BLOCKS_NUM*IND_NUM)*(i+1))-1 downto (BLOCKS_NUM*IND_NUM)*i),
                    A_vector    => A_vector_int,

                    sparsity_lock   => sparsity_lock,

                    valid_in    => valid_in_int,
                    tlast_in    => tlast_in_core,

                    Cout        => Cout_int((i+1)*EL_SIZE*BLOCKS_NUM-1 downto i*EL_SIZE*BLOCKS_NUM),
                    Cvalid      => Cvalid_int,
                    Ctlast      => Ctlast_int
                );
        end generate C_CORE_INST_FIRST;
        
        C_CORE_INST_REST : if i /=0 generate

            C_CORE_REST : c_core
                generic map(
                    EL_SIZE     => EL_SIZE,
                    W_IDX       => W_IDX,
                    A_IDX       => A_IDX,
                    BLOCKS_NUM  => BLOCKS_NUM,
                    IND_NUM     => IND_NUM
                )
                port map(
                    clk         => clk,
                    resetn      => resetn,

                    W_row       => W_row_int(((W_IDX*EL_SIZE)*(i+1))-1 downto (W_IDX*EL_SIZE)*i),
                    Indices     => Indices_int(((BLOCKS_NUM*IND_NUM)*(i+1))-1 downto (BLOCKS_NUM*IND_NUM)*i),
                    A_vector    => A_vector_int,

                    sparsity_lock   => sparsity_lock,

                    valid_in    => valid_in_int,
                    tlast_in    => tlast_in_core,

                    Cout        => Cout_int((i+1)*EL_SIZE*BLOCKS_NUM-1 downto i*EL_SIZE*BLOCKS_NUM),
                    Cvalid      => open,
                    Ctlast      => open
                );
        end generate C_CORE_INST_REST;
    end generate CORES_GEN;


end architecture two2N_arch;