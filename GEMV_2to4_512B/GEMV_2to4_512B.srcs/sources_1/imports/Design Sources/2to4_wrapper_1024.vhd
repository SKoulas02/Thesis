library IEEE;
use IEEE.std_logic_1164.ALL;
use IEEE.numeric_std.all;
use IEEE.math_real.all;

entity wrapper_2to4 is
    generic(
        EL_SIZE     : integer := 16;    -- Bit size of each element
        BUS_EL      : integer := 32;    -- Maximum number of elements on bus

        A_IDX       : integer := 2;     -- Number of matrix elements
        B_IDX       : integer := 4;     -- Number of vector elements
        IND_NUM     : integer := 3;     -- Number of indices Bits

        A_ROWS      : integer := 1024   -- Number of Rows of Matrix A
    );
    port(
        clk         : in  std_logic;
        resetn      : in  std_logic;

        -- B vector AXI-Stream slave
        B_tdata     : in  std_logic_vector((BUS_EL*EL_SIZE)-1 downto 0);
        B_tvalid    : in  std_logic;
        B_tlast     : in  std_logic;
        B_tready    : out std_logic;

        -- A matrix AXI-Stream slave
        A_tdata     : in  std_logic_vector((BUS_EL*EL_SIZE)-1 downto 0);
        A_tvalid    : in  std_logic;
        A_tready    : out std_logic;

        -- Indices AXI-Stream slave
        ind_tdata   : in  std_logic_vector ( (2 ** integer(floor(log2(real((B_IDX*EL_SIZE)/IND_NUM))))) * IND_NUM*BUS_EL/B_IDX -1 downto 0);
        ind_tvalid  : in  std_logic;
        ind_tready  : out std_logic;

        -- C output AXI-Stream master
        C_tdata     : out std_logic_vector(EL_SIZE-1 downto 0);
        C_tvalid    : out std_logic;
        C_tlast     : out std_logic;
        C_tready    : in  std_logic
    );
end entity wrapper_2to4;


architecture wrapper_arch of wrapper_2to4 is

    component two2four is
    generic(
        EL_SIZE     : integer := 16;    -- Bit size of each element
        BUS_EL      : integer := 32;    -- Maximum number of elements on bus

        A_IDX       : integer := 2;     -- Number of matrix elements
        B_IDX       : integer := 4;     -- Number of vector elements
        IND_NUM     : integer := 3;     -- Number of indices Bits

        A_ROWS      : integer := 1024   -- Number of Rows of Matrix A
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        B_in        : in std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
        B_valid_in  : in std_logic;
        tlast_in    : in std_logic;

        A_in        : in std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
        A_valid     : in std_logic;
        
        indices     : in std_logic_vector ( (2 ** integer(floor(log2(real((B_IDX*EL_SIZE)/IND_NUM))))) * IND_NUM*BUS_EL/B_IDX -1 downto 0);
        ind_valid   : in std_logic;

        Cout        : out std_logic_vector (EL_SIZE-1 downto 0);
        Cvalid      : out std_logic;
        Ctlast      : out std_logic
    );
    end component two2four;

    component skidbuffer is
	generic (
		OPT_OUTREG      : std_logic := '1';
		OPT_PASSTHROUGH : std_logic := '0';
		DATA_WIDTH      : natural   := 8
		);

	port (
		aclk       : in std_logic;
		arst       : in std_logic; -- active high
		-- SLAVE IF
		slv_tdata  : in  std_logic_vector(DATA_WIDTH-1 downto 0);
		slv_tready : out std_logic;
		slv_tvalid : in  std_logic;
		-- MASTER IF
		mst_tdata  : out std_logic_vector(DATA_WIDTH-1 downto 0);
		mst_tready : in  std_logic;
		mst_tvalid : out std_logic
	);
    end component skidbuffer;

    signal arst         : std_logic := '0';

    -- A matrix skid buffer signals (master side feeds two2four)
    signal skb_A_data       : std_logic_vector(BUS_EL*EL_SIZE-1 downto 0) := (others => '0');
    signal skb_A_valid      : std_logic := '0';

    -- B vector skid buffer signals (tlast packed into bit 0 internally)
    signal skb_B_slv_tdata  : std_logic_vector(BUS_EL*EL_SIZE downto 0) := (others => '0');
    signal skb_B_data_full  : std_logic_vector(BUS_EL*EL_SIZE downto 0) := (others => '0');
    signal skb_B_data       : std_logic_vector(BUS_EL*EL_SIZE-1 downto 0) := (others => '0');
    signal skb_B_tlast      : std_logic := '0';
    signal skb_B_valid      : std_logic := '0';

    -- Indices skid buffer signals (master side feeds two2four)
    signal skb_ind_data     : std_logic_vector( (2 ** integer(floor(log2(real((B_IDX*EL_SIZE)/IND_NUM))))) * IND_NUM*BUS_EL/B_IDX -1 downto 0) := (others => '0');
    signal skb_ind_valid    : std_logic := '0';

    -- C output: two2four core outputs feed slave side of output skid buffer
    signal core_Cout        : std_logic_vector(EL_SIZE-1 downto 0) := (others => '0');
    signal core_Cvalid      : std_logic := '0';
    signal core_Ctlast      : std_logic := '0';
    signal C_slv_tready     : std_logic := '0';
    signal skb_C_slv_tdata  : std_logic_vector(EL_SIZE downto 0) := (others => '0');
    signal skb_C_mst_tdata  : std_logic_vector(EL_SIZE downto 0) := (others => '0');

begin

    arst <= not resetn;

    -- Pack B input data + tlast for the input skid buffer
    skb_B_slv_tdata <= B_tdata & B_tlast;

    -- Unpack B data and tlast from the master side of the input skid buffer
    skb_B_data  <= skb_B_data_full(BUS_EL*EL_SIZE downto 1);
    skb_B_tlast <= skb_B_data_full(0);

    -- Pack core C output + Ctlast for the output skid buffer
    skb_C_slv_tdata <= core_Cout & core_Ctlast;

    -- Unpack the master side of the output skid buffer to entity C ports
    C_tdata <= skb_C_mst_tdata(EL_SIZE downto 1);
    C_tlast <= skb_C_mst_tdata(0);

    A_MATRIX_INSTANCE : skidbuffer
    generic map(
        OPT_OUTREG      => '1',
        OPT_PASSTHROUGH => '0',
        DATA_WIDTH      => BUS_EL*EL_SIZE
    )
    port map(
        aclk       => clk,
        arst       => arst,

        slv_tdata  => A_tdata,
        slv_tvalid => A_tvalid,
        slv_tready => A_tready,

        mst_tdata  => skb_A_data,
        mst_tvalid => skb_A_valid,
        mst_tready => '1'
    );

    B_VECTOR_INSTANCE : skidbuffer
    generic map(
        OPT_OUTREG      => '1',
        OPT_PASSTHROUGH => '0',
        DATA_WIDTH      => BUS_EL*EL_SIZE + 1
    )
    port map(
        aclk       => clk,
        arst       => arst,

        slv_tdata  => skb_B_slv_tdata,
        slv_tvalid => B_tvalid,
        slv_tready => B_tready,

        mst_tdata  => skb_B_data_full,
        mst_tvalid => skb_B_valid,
        mst_tready => '1'
    );

    INDICES_INSTANCE : skidbuffer
    generic map(
        OPT_OUTREG      => '1',
        OPT_PASSTHROUGH => '0',
        DATA_WIDTH      => (2 ** integer(floor(log2(real((B_IDX*EL_SIZE)/IND_NUM))))) * IND_NUM*BUS_EL/B_IDX
    )
    port map(
        aclk       => clk,
        arst       => arst,

        slv_tdata  => ind_tdata,
        slv_tvalid => ind_tvalid,
        slv_tready => ind_tready,

        mst_tdata  => skb_ind_data,
        mst_tvalid => skb_ind_valid,
        mst_tready => '1'
    );

    TWO2FOUR_INSTANCE : two2four
    generic map(
        EL_SIZE     => EL_SIZE,
        BUS_EL      => BUS_EL,
        A_IDX       => A_IDX,
        B_IDX       => B_IDX,
        IND_NUM     => IND_NUM,
        A_ROWS      => A_ROWS
    )
    port map(
        clk         => clk,
        resetn      => resetn,

        B_in        => skb_B_data,
        B_valid_in  => skb_B_valid,
        tlast_in    => skb_B_tlast,

        A_in        => skb_A_data,
        A_valid     => skb_A_valid,

        indices     => skb_ind_data,
        ind_valid   => skb_ind_valid,

        Cout        => core_Cout,
        Cvalid      => core_Cvalid,
        Ctlast      => core_Ctlast
    );

    C_OUTPUT_INSTANCE : skidbuffer
    generic map(
        OPT_OUTREG      => '1',
        OPT_PASSTHROUGH => '0',
        DATA_WIDTH      => EL_SIZE + 1
    )
    port map(
        aclk       => clk,
        arst       => arst,

        slv_tdata  => skb_C_slv_tdata,
        slv_tvalid => core_Cvalid,
        slv_tready => C_slv_tready,

        mst_tdata  => skb_C_mst_tdata,
        mst_tvalid => C_tvalid,
        mst_tready => C_tready
    );


end architecture wrapper_arch;
