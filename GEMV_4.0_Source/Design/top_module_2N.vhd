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
        IND_BITS    : integer := 640      -- useful index bits (remainder is padding)
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        -- Weights : 8 PCs
        s_axis_w_tdata  : in  std_logic_vector(W_PCS*PC_WIDTH-1 downto 0);
        s_axis_w_tvalid : in  std_logic_vector(W_PCS-1 downto 0);
        s_axis_w_tready : out std_logic_vector(W_PCS-1 downto 0);

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
        m_axis_c_tvalid : out std_logic;
        m_axis_c_tlast  : out std_logic;
        m_axis_c_tready : in std_logic
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

            -- ---- write side: from HBM, one strobe per pseudo-channel ----
            w_din    : in  std_logic_vector(W_PCS*PC_WIDTH-1 downto 0);
            w_wr     : in  std_logic_vector(W_PCS-1 downto 0);
            w_full   : out std_logic_vector(W_PCS-1 downto 0);

            ind_din  : in  std_logic_vector(IND_PCS*PC_WIDTH-1 downto 0);
            ind_wr   : in  std_logic_vector(IND_PCS-1 downto 0);
            ind_full : out std_logic_vector(IND_PCS-1 downto 0);

            -- ---- read side: to compute, one aligned word ----
            rd_en        : in  std_logic;                                    -- consumer pop
            ready        : out std_logic;                                    -- all PCs have a beat
            W_out        : out std_logic_vector(W_PCS*PC_WIDTH-1 downto 0);  -- 2048
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

            -- ---- write side: from HBM, one strobe + rlast per pseudo-channel ----
            a_din    : in  std_logic_vector(A_PCS*PC_WIDTH-1 downto 0);
            a_wr     : in  std_logic_vector(A_PCS-1 downto 0);
            a_tlast  : in  std_logic_vector(A_PCS-1 downto 0);   -- per-PC end-of-vector (rlast)
            a_full   : out std_logic_vector(A_PCS-1 downto 0);

            -- ---- read side: to compute / replay buffer, one aligned word ----
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

    -- Internal Signals Vector FIFO


begin    

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

            w_din       => s_axis_w_tdata,
            w_wr        => s_axis_w_tvalid,
            w_full      => s_axis_w_tready,

            ind_din     => s_axis_ind_tdata,
            ind_wr      => s_axis_ind_tvalid,
            ind_full    => s_axis_ind_tready,

            rd_en           => rd_en_int,
            ready           => ready_int,
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

            a_din       => s_axis_a_tdata,
            a_wr        => s_axis_a_tvalid,
            a_tlast     => s_axis_a_tlast,
            a_full      => s_axis_a_tready,

            rd_en           => rd_en_int,
            ready           => ready_int,
            A_vector_out    => A_vector_int,
            tlast_out       => tlast_int
        );

    ACTIVATION_CYCLE_INST : vector_cycle_512
        generic map(
            PC_WIDTH   => PC_WIDTH,
            A_PCS      => A_PCS
        )
        port map(
            clk         => clk,
            resetn      => resetn,

            A_vector    => A_vector_int,
            tlast_in    => tlast_int,
            valid_in    => valid_int,

            rd_en       => rd_en_int,
            cycle_en    => cycle_en_int,

            ready           => ready_int,
            A_vector_out    => A_vector_int,
            tlast_out       => tlast_int
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

                    W_row       => W_row_int,
                    Indices     => Indices_int,
                    A_vector    => A_vector_int,

                    sparsity_lock   => Sparsity_lock,

                    valid_in    => valid_in_int,
                    tlast_in    => tlast_in_int,

                    Cout        => Cout_int((i+1)*EL_SIZE*BLOCKS_NUM-1 downto i*EL_SIZE*BLOCKS_NUM),
                    Cvalid      => Cvalid_int(i),
                    Ctlast      => Ctlast_int(i)
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

                    W_row       => W_row_int,
                    Indices     => Indices_int,
                    A_vector    => A_vector_int,

                    sparsity_lock   => Sparsity_lock,

                    valid_in    => valid_in_int,
                    tlast_in    => tlast_in_int,

                    Cout        => Cout_int((i+1)*EL_SIZE*BLOCKS_NUM-1 downto i*EL_SIZE*BLOCKS_NUM),
                    Cvalid      => Cvalid_int(i),
                    Ctlast      => Ctlast_int(i)
                );
        end generate C_CORE_INST_REST;
    end generate CORES_GEN;


end architecture two2N_arch;