library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
 
entity top_module is
   generic(
      ROWS  : integer := 1;
      COLS  : integer := 16;
      EL_WIDTH : integer := 16;
      EL_NUM   : integer := 16
   );
   port(
      clk      : in std_logic;
      resetn   : in std_logic;
      
      a_bus    : in std_logic_vector((EL_NUM*EL_WIDTH)-1 downto 0);
      a_valid  : in std_logic;
      
      b_bus    : in std_logic_vector((ROWS*EL_WIDTH)-1 downto 0);
      b_valid  : in std_logic;
      top_tlast: in std_logic;

      c_array_out : out std_logic_vector((ROWS*COLS*EL_WIDTH)-1 downto 0);
      c_valid     : out std_logic_vector(COLS-1 downto 0);
      c_tlast     : out std_logic
   );
end entity top_module;

architecture top_arch of top_module is

   component c_array is
   generic(
      ROWS    : integer := ROWS;
      COLS    : integer := COLS;
      EL_WIDTH: integer := EL_WIDTH
   );
   port(
      clk         : in std_logic;
      resetn      : in std_logic;
      
      row_data    : in std_logic_vector ((ROWS*EL_WIDTH)-1 downto 0);
      column_data : in std_logic_vector ((COLS*EL_WIDTH)-1 downto 0);
      row_valid   : in std_logic_vector (ROWS-1 downto 0);
      column_valid: in std_logic_vector (COLS-1 downto 0);
      tlast       : in std_logic;

      C_out       : out std_logic_vector ((COLS*ROWS*EL_WIDTH)-1 downto 0);
      C_valid_out : out std_logic_vector (COLS-1 downto 0);
      C_tlast_out : out std_logic
   );
   end component c_array;

   component bus2fifo is
      generic(
         EL_NUM      : integer := EL_NUM;       -- Elements of Input Bus
         EL_WIDTH    : integer := EL_WIDTH;     -- Width of each element
         DIM         : integer := COLS          -- Number of FIFO Modules and DIM of Matrix
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
   end component bus2fifo;

   component bus_rows is
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
   end component bus_rows;

   type states is (Reset,Rising);
   signal state : states := Reset;

   signal rd_en_rows : std_logic;
   signal valid_rows : std_logic_vector (ROWS-1 downto 0);
   signal empty_rows : std_logic_vector (ROWS-1 downto 0);
   signal data_rows  : std_logic_vector ((ROWS*EL_WIDTH)-1 downto 0);
   
   signal rd_en_cols : std_logic_vector (COLS-1 downto 0);
   signal valid_cols : std_logic_vector (COLS-1 downto 0);
   signal empty_cols : std_logic_vector (COLS-1 downto 0);
   signal data_cols  : std_logic_vector ((COLS*EL_WIDTH)-1 downto 0);

   signal tlast_int  : std_logic := '0';
   signal tlast_array: std_logic := '0';

begin
   
   MAIN_PROC : process(clk)
   begin
      if rising_edge(clk) then
         if resetn = '0' then
            rd_en_rows  <= '0';
            rd_en_cols  <= (others => '0');
            state       <= Reset;
            tlast_array <= '0';
         else
            case state is
            when Reset =>
               if tlast_int = '1' then
                  tlast_array <= '1';
                  state <= Rising;
               end if;
            
            when Rising =>
               tlast_array <= '0';
               if tlast_int = '0' then
                  state <= Reset;
               end if;

            when others =>
            end case;
         
            -- Read Enable Logic
            if empty_rows(0) = '0' AND empty_cols(0) = '0' then
               rd_en_rows     <= '1';
               rd_en_cols     <= rd_en_cols (COLS-2 downto 0) & '1';
            else
               rd_en_rows     <= '0';
               rd_en_cols     <= rd_en_cols (COLS-2 downto 0) & '0';
            end if;
         end if;
      end if;
   end process;
   
   C_ARRAY_INSTANCE : c_array
   generic map(
      ROWS  => ROWS,
      COLS  => COLS,
      EL_WIDTH => EL_WIDTH
   )
   port map(
      clk      => clk,
      resetn   => resetn,

      row_data       => data_rows,
      column_data    => data_cols,
      row_valid      => valid_rows,
      column_valid   => valid_cols,
      tlast          => tlast_array,

      C_out          => c_array_out, 
      C_valid_out    => c_valid,
      C_tlast_out    => c_tlast     
   );
   
   
   ROWS_FIFO   : bus_rows
   generic map(
      EL_WIDTH => EL_WIDTH
   )
   port map(
      clk      => clk,
      resetn   => resetn,

      valid_in => b_valid,
      data_in  => b_bus,
      rd_en_i  => rd_en_rows,

      tlast       => top_tlast,
      tlast_out   => tlast_int,

      valid_out   => valid_rows(0),
      empty       => empty_rows(0),
      data_out    => data_rows
   );

   COLS_FIFO   : bus2fifo
   generic map(
      EL_NUM   => EL_NUM,
      EL_WIDTH => EL_WIDTH,
      DIM      => COLS
   )
   port map(
      clk      => clk,
      resetn   => resetn,

      valid_in => a_valid,
      data_in  => a_bus,
      rd_en_i  => rd_en_cols,

      valid_out   => valid_cols,
      empty_i     => empty_cols,
      data_out    => data_cols
   );

end architecture top_arch;
