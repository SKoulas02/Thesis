library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity bus2fifo is
    generic(
        EL_NUM      : integer := 32;    -- Elements of Input Bus
        EL_WIDTH    : integer := 16;    -- Width of each element
        DIM        : integer := 32     -- Number of FIFO Modules and DIM of Matrix
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        valid_in    : in std_logic;
        data_in     : in std_logic_vector ((EL_NUM * EL_WIDTH)-1 downto 0);
        rd_en_i     : in std_logic_vector (DIM-1 downto 0);    -- Read Enable per FIFO
        
        valid_out   : out std_logic_vector (DIM-1 downto 0);   -- Valid out flag for each FIFO
        empty_i     : out std_logic_vector (DIM-1 downto 0);   -- Empty flag for each FIFO
        data_out    : out std_logic_vector ((DIM*EL_WIDTH)-1 downto 0)
    );

end entity bus2fifo;

architecture bus_arch of bus2fifo is

    component fifo_gen IS
    PORT (
        clk     : in std_logic;
        srst    : in std_logic;
        
        din     : in std_logic_vector (15 downto 0);
        wr_en   : in std_logic;
        rd_en   : in std_logic;
        dout    : out std_logic_vector (15 downto 0);
        
        empty   : out std_logic;
        valid   : out std_logic
    );
    end component fifo_gen;

    type din_array is array (0 to DIM-1) of std_logic_vector(EL_WIDTH-1 downto 0);
    signal din  : din_array;

    signal iteration  : integer range 0 to ((DIM/EL_NUM)-1) := 0;
    signal srst     : std_logic;
    signal wr_en    : std_logic_vector (DIM-1 downto 0);
    
begin

    srst <= NOT resetn;

    MAIN_PROCESS : process(clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then
                wr_en   <= (others => '0');
                iteration <= 0;
            else
                wr_en   <= (others => '0');
                if valid_in = '1' then
                    for i in 0 to EL_NUM-1 loop
                        din(i+EL_NUM*iteration) <= data_in(((i+1)*EL_WIDTH)-1 downto i*EL_WIDTH);
                        wr_en(i+EL_NUM*iteration) <= '1';
                    end loop;
                    if iteration = (DIM/EL_NUM)-1 then
                        iteration <= 0;
                    else
                        iteration <= iteration + 1;
                    end if;
                end if;
            end if;
        end if;
    end process;


    FIFO_GEN_INSTANCE: for i in 0 to DIM-1 generate
        
        FIFO: fifo_gen
        port map(
            clk     => clk,
            srst    => srst,
            din     => din(i),
            wr_en   => wr_en(i),
            rd_en   => rd_en_i(i),
            dout    => data_out(((i+1)*EL_WIDTH)-1 downto (i*EL_WIDTH)),
            empty   => empty_i(i),
            valid   => valid_out(i)
        );
    end generate FIFO_GEN_INSTANCE;

end architecture bus_arch;