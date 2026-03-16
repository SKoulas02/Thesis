library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity bus_rows is
    generic(
        EL_WIDTH    : integer := 16     -- Width of each element
    );
    port(
        clk         : in std_logic;
        resetn      : in std_logic;

        valid_in    : in std_logic;
        data_in     : in std_logic_vector (EL_WIDTH-1 downto 0);
        rd_en_i     : in std_logic;                            -- Read Enable per FIFO
        
        tlast       : in std_logic;
        tlast_out   : out std_logic;
        
        valid_out   : out std_logic;                           -- Valid out flag for each FIFO
        empty       : out std_logic;                           -- Empty flag for each FIFO
        data_out    : out std_logic_vector (EL_WIDTH-1 downto 0)
    );

end entity bus_rows;

architecture bus_rows_arch of bus_rows is

    component fifo_gen_extended IS
    PORT (
        clk     : in std_logic;
        srst    : in std_logic;
        
        din     : in std_logic_vector (16 downto 0);
        wr_en   : in std_logic;
        rd_en   : in std_logic;
        dout    : out std_logic_vector (16 downto 0);
        
        empty   : out std_logic;
        valid   : out std_logic
    );
    end component fifo_gen_extended;

    signal din  : std_logic_vector (EL_WIDTH downto 0);     -- Extended
    signal dout : std_logic_vector (EL_WIDTH downto 0);

    signal srst     : std_logic;
    signal wr_en    : std_logic;

begin

    srst <= NOT resetn;
    data_out <= dout(EL_WIDTH downto 1);
    tlast_out <= dout(0);
    
    MAIN_PROCESS : process(clk)
    begin
        if rising_edge(clk) then
            if resetn = '0' then
                wr_en   <= '0';
            else
                if valid_in = '1' then
                    wr_en <= '1';
                    din <= data_in & tlast;
                else
                    wr_en <= '0';
                end if;
            end if;
        end if;
    end process;


    
    FIFO: fifo_gen_extended
    port map(
        clk     => clk,
        srst    => srst,
        din     => din,
        wr_en   => wr_en,
        rd_en   => rd_en_i,
        dout    => dout,
        empty   => empty,
        valid   => valid_out
    );
    
end architecture bus_rows_arch;