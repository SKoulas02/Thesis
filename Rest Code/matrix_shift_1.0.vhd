library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity matrix_shift is
    generic(
        DATAW   : integer := 16;
        BUSW    : integer := 512;
        N       : integer := 32
    );
    port(
        clk     : in std_logic;
        resetn  : in std_logic;
        shift   : in std_logic;
        DATA_IN : in std_logic_vector (BUSW-1 downto 0);
        DATA_OUT: out std_logic_vector ((N*DATAW)-1 downto 0)
    );
end entity matrix_shift;

architecture matrix_shift_arch of matrix_shift is
    -- Depth = 16
    component shift_ram is
        port(
            d   : in std_logic_vector (15 downto 0);
            clk : in std_logic;
            ce  : in std_logic;
            sclr: in std_logic;
            q   : out std_logic_vector (15 downto 0)
        );
    end component;

    constant iterations     : natural := (N*DATAW/BUSW)-1;

    type ram_wire_array is array (0 to N-1) of std_logic_vector (15 downto 0);
    signal wires : ram_wire_array;

    type ce_array is array (0 to iterations) of std_logic;
    signal ce_groups        : ce_array;

    signal cycle_sel        : natural range 0 to iterations := 0;
    signal reset            : std_logic;
  
begin

    reset <= NOT resetn;

    process (clk)
    begin
        if rising_edge(clk) then
            if resetn ='0' then
                cycle_sel <= 0;
            else
                if shift = '1' then
                    if cycle_sel = iterations then
                        cycle_sel <= 0;
                    else
                        cycle_sel <= cycle_sel +1;
                    end if;
                end if;
            end if;
        end if;
    end process;


    GEN_CE: for k in 0 to iterations generate
        ce_groups(k) <= '1' when (shift = '1' AND cycle_sel = k) else '0';
    end generate GEN_CE;


    GEN_SHIFT_REGS: for i in 0 to N-1 generate

        constant group_idx  : integer := i/(BUSW/DATAW);
        constant bus_idx    : integer := i MOD (BUSW/DATAW);

    begin

        SHIFT_REG : shift_ram
            port map (
                d   => DATA_IN ( ((bus_idx + 1) * DATAW)-1 downto bus_idx*DATAW),
                clk => clk,
                ce  => ce_groups(group_idx),
                sclr=> reset,
                q   => wires(i)
            ); 
    end generate GEN_SHIFT_REGS;


    GEN_OUT : for i in 0 to N-1 generate

        DATA_OUT( ((i+1)*DATAW)-1  downto (i*DATAW) ) <= wires(i);

    end generate GEN_OUT;
    
end architecture matrix_shift_arch;
