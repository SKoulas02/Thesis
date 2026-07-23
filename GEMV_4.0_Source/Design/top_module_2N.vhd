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


    -- Internal Signals Weights FIFO

    signal rd_en_int_w     : std_logic;
    signal ready_int_w     : std_logic;
    signal tlast_int_w     : std_logic;   -- end-of-calc marker (last weight beat popped)
    signal W_row_int       : std_logic_vector ((W_PCS*PC_WIDTH)-1 downto 0);
    signal Indices_int     : std_logic_vector (IND_BITS-1 downto 0);
    signal Sparsity        : std_logic_vector (1 downto 0);

    -- Internal Signals Activation FIFO

    signal rd_en_int_a     : std_logic;
    signal ready_int_a     : std_logic;
    signal A_vector_int_a  : std_logic_vector ((A_PCS*PC_WIDTH)-1 downto 0);
    signal tlast_int_a     : std_logic;

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
    signal tlast_end        : std_logic := '0';
    signal settle           : std_logic := '0';   -- lap-boundary bubble: hold one cycle so the weights-FIFO
                                                   -- head advances to the next group's first beat before its
                                                   -- Sparsity is sampled (needed for runtime 2:M reconfiguration)
    signal m_axis_tlast     : std_logic := '0';

    signal flush_2to32      : std_logic;          -- combinational 2:32 vector-end flush (no-freeze path)
    signal tlast_in_core    : std_logic;          -- cores' tlast_in = registered terminals OR 2:32 flush
begin

    m_axis_tlast <= tlast_end AND Ctlast_int;

    -- Activation window mux: COMBINATIONAL so it tracks the same FIFO head cycle as
    -- the combinational weights bus (W_row_int). A registered mux lags one cycle and
    -- mispairs windows when 2:32 (no freeze) reads a new window every cycle.
    A_vector_int <= A_vector_int_c when cycle_en_int = '1' else A_vector_int_a;

    -- 2:32 combinational flush: with no freeze, the vector-end window is processed the
    -- same cycle its FIFO head is popped, so its tlast must reach the cores co-timed.
    -- (The registered tlast_in_int decision is one cycle late for back-to-back reads.)
    -- valid_in_int guards idle/settle cycles and makes the FIRST window commit (NWIN=1).
    flush_2to32 <= '1' when (Sparsity = "11" AND valid_in_int = '1' AND
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
                tlast_end       <= '0';
                settle          <= '0';

            else

                if tlast_int_w = '1' then
                    tlast_end <= '1';
                elsif tlast_end = '1' AND Ctlast_int = '1' then
                    tlast_end <= '0';   -- final tagged beat emitted; clear for the next run
                end if;

                if settle = '1' then
                    -- One-cycle lap-boundary bubble. The previous lap's terminal popped its
                    -- last weight beat this-1 cycle; hold now so the weights-FIFO head advances
                    -- to the next group's FIRST beat before its Sparsity is sampled. Without this
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
                        elsif ready_int_w = '1' AND ready_int_a = '1' AND prog_full_int = '0'
                           AND (Sparsity = "11" OR tlast_int_w = '0') then  -- inputs ready + output not near-full + not past end

                            rd_en_int_w <= '1';
                            rd_en_int_a <= '1';
                            sparsity_lock <= '0';
                            valid_in_int <= '1';
                            last_win <= tlast_int_a;   -- latch whether the window being loaded is the last

                            case Sparsity is
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
                           AND (Sparsity = "11" OR tlast_int_w = '0') then  -- inputs ready + output not near-full + not past end

                            rd_en_int_w <= '1';
                            rd_en_int_c <= '1';
                            sparsity_lock <= '0';
                            valid_in_int <= '1';
                            last_win <= tlast_int_c;   -- latch whether the window being loaded is the last

                            case Sparsity is
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

            rd_en           => rd_en_int_w,
            ready           => ready_int_w,
            tlast_out       => tlast_int_w,
            W_out           => W_row_int,
            indices_out     => Indices_int,
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

            rd_en           => rd_en_int_a,
            ready           => ready_int_a,
            A_vector_out    => A_vector_int_a,
            tlast_out       => tlast_int_a
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