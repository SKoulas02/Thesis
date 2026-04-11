library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

-- ----------------------------------------------------------------------------
-- Engineer: Sozos Koulas @ National Technical University of Athens
-- 
-- Description:
-- This Module is used to calculate the output of a row of 2:4 GEMV operations.
-- Input is one row of elements of Matrix A and row*2 elements of Vector B.
-- It is made up of c blocks that are fully pipelined.
-- The module can be reused for different GEMV operations and has a through-put of 1 cycle.
-- ----------------------------------------------------------------------------

entity c_block_row is
    generic(
        EL_SIZE     : integer := 16;    -- Bit size of each element
        A_IDX       : integer := 2;     -- Number of matrix elements
        B_IDX       : integer := 4;     -- Number of vector elements
        BUS_EL      : integer := 8;     -- Maximum number of elements on bus
        IND_NUM     : integer := 3      -- Number of Indices Bits
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;
        
        B_vector_in : in std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
        B_valid_in  : in std_logic;
        tlast_in    : in std_logic;

        A_row       : in std_logic_vector ((BUS_EL*EL_SIZE/2)-1 downto 0);
        block_flag  : in std_logic;
        Indices     : in std_logic_vector ((2*IND_NUM)-1 downto 0);
        
        B_vector_out: out std_logic_vector ((BUS_EL*EL_SIZE)-1 downto 0);
        B_valid_out : out std_logic;
        tlast_out   : out std_logic;

        Cout        : out std_logic_vector ((BUS_EL*EL_SIZE/B_IDX)-1 downto 0);
        Cvalid      : out std_logic;
        Ctlast      : out std_logic
    );
end entity c_block_row;


architecture c_block_row_arch of c_block_row is

    component c_block is
        generic(
        EL_SIZE : integer := 16;    -- Bit size of each element
        A_IDX   : integer := 2;     -- Number of matrix elements
        B_IDX   : integer := 4;     -- Number of vector elements
        IND_NUM : integer := 3      -- Number of Indices Bits
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        A_in        : in std_logic_vector ((EL_SIZE*A_IDX)-1 downto 0);     -- Matrix Input
        block_flag  : in std_logic;
        Indices     : in std_logic_vector (IND_NUM-1 downto 0);

        B_in        : in std_logic_vector ((EL_SIZE*B_IDX)-1 downto 0);     -- Vector Input
        B_valid     : in std_logic;
        
        tlast_in    : in std_logic;

        Bout        : out std_logic_vector ((EL_SIZE*B_IDX)-1 downto 0);     -- Vector Output
        tlast_out   : out std_logic;
        B_valid_out : out std_logic;

        Cout        : out std_logic_vector (EL_SIZE-1 downto 0);        -- Calculated Output Element
        Cvalid      : out std_logic;
        Ctlast      : out std_logic
    );
    end component c_block;

begin

    -- Generate c_block instances according to the number of elements that can fit on the bus.
    -- The First block instance has the tlast and valid signals connected to the output and the other blocks calculate
    
    C_BLOCK_GEN : for i in 0 to (BUS_EL/B_IDX)-1 generate
        
        C_FIRST : if i = 0 generate

            C_BLOCK_FIRST : c_block
            generic map(
                EL_SIZE => EL_SIZE, -- Bit size of each element
                A_IDX   => A_IDX,   -- Number of matrix elements
                B_IDX   => B_IDX,   -- Number of vector elements
                IND_NUM => IND_NUM  -- Number of Indices Bits
            )
            port map(
                clk         => clk,
                resetn      => resetn,

                A_in        => A_row (EL_SIZE*A_IDX*(i+1)-1 downto i*EL_SIZE*A_IDX),        -- Matrix Input for Each Block
                block_flag  => block_flag,
                Indices     => Indices (((i+1)*IND_NUM)-1 downto i*IND_NUM),                  -- Indices for Each Block

                B_in        => B_vector_in (EL_SIZE*B_IDX*(i+1)-1 downto i*EL_SIZE*B_IDX),  -- Vector Input for Each Block
                B_valid     => B_valid_in,
                
                tlast_in    => tlast_in,

                Bout        => B_vector_out (EL_SIZE*B_IDX*(i+1)-1 downto i*EL_SIZE*B_IDX),     -- Vector Output for Each Block
                tlast_out   => tlast_out,
                B_valid_out => B_valid_out,

                Cout        => Cout ((i+1)*EL_SIZE-1 downto i*EL_SIZE),        -- Calculated Output Element
                Cvalid      => Cvalid,
                Ctlast      => Ctlast
            );
        end generate;

        C_ELSE : if i > 0 generate

            C_BLOCK_INSTANCE : c_block
            generic map(
                EL_SIZE => EL_SIZE, -- Bit size of each element
                A_IDX   => A_IDX,   -- Number of matrix elements
                B_IDX   => B_IDX,   -- Number of vector elements
                IND_NUM => IND_NUM  -- Number of Indices Bits
            )
            port map(
                clk         => clk,
                resetn      => resetn,

                A_in        => A_row (EL_SIZE*A_IDX*(i+1)-1 downto i*EL_SIZE*A_IDX),     -- Matrix Input for Each Block
                block_flag  => block_flag,
                Indices     => Indices (((i+1)*IND_NUM)-1 downto i*IND_NUM),            -- Indices for Each Block

                B_in        => B_vector_in (EL_SIZE*B_IDX*(i+1)-1 downto i*EL_SIZE*B_IDX),    -- Vector Input for Each Block
                B_valid     => B_valid_in,
                
                tlast_in    => tlast_in,

                Bout        => B_vector_out (EL_SIZE*B_IDX*(i+1)-1 downto i*EL_SIZE*B_IDX),     -- Vector Output for Each Block
                tlast_out   => open,
                B_valid_out => open,

                Cout        => Cout ((i+1)*EL_SIZE-1 downto i*EL_SIZE),        -- Calculated Output Element
                Cvalid      => open,
                Ctlast      => open
            );
        end generate;
    end generate;
end architecture c_block_row_arch;