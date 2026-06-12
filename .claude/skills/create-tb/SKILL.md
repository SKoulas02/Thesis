---
description: Generate a VHDL testbench for the currently open VHDL file. Use when the user says "create testbench", "create tb", "generate tb", or invokes /create-tb.
---

## Your task

Generate a synthesis-ready VHDL testbench for the currently open VHDL design file.

## Step 1 — Identify the source file

The user has a VHDL file open in the IDE. Read it using the Read tool. The file path is shown in the IDE context.

## Step 2 — Parse the entity

From the file extract:
- **Entity name** (e.g., `c_block`)
- **Generic declarations** with their default values (e.g., `EL_SIZE : integer := 16`)
- **Port declarations** — names, directions (`in`/`out`/`inout`), and types

## Step 3 — Resolve concrete port widths

The testbench entity has **no generics and no ports**. All port widths that reference generics must be resolved to concrete integer values using the generic defaults.

Examples using `EL_SIZE=16`, `W_IDX=2`, `A_IDX=32`, `IND_NUM=10`:
- `std_logic_vector((EL_SIZE*W_IDX)-1 downto 0)` → `std_logic_vector(31 downto 0)`
- `std_logic_vector(IND_NUM-1 downto 0)` → `std_logic_vector(9 downto 0)`
- `std_logic_vector((EL_SIZE*A_IDX)-1 downto 0)` → `std_logic_vector(511 downto 0)`
- `std_logic_vector(EL_SIZE-1 downto 0)` → `std_logic_vector(15 downto 0)`

Always evaluate arithmetic expressions fully to a plain integer.

## Step 4 — Determine the output path

The design file is located in a folder such as `<project_root>/Design/` or similar.
The Simulation folder is the sibling directory called `Simulation/` (one level up from `Design/`, then into `Simulation/`).

Output file path: `<sibling Simulation folder>/<EntityName>_TB.vhd`

Use PowerShell to check that the Simulation folder exists before writing:
`Test-Path "<simulation_folder>"`

## Step 5 — Generate the testbench

Follow this exact structure:

```vhdl
library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity <EntityName>_TB is
end entity <EntityName>_TB;

architecture <EntityName>_TB_arch of <EntityName>_TB is

    -- Component declaration
    component <EntityName> is
    port(
        <port_name> : <direction> <concrete_type>;
        ...
        <last_port>  : <direction> <concrete_type>
    );
    end component <EntityName>;

    -- Clock and reset
    constant CLK_PERIOD : time := 10 ns;
    signal clk    : std_logic := '0';
    signal resetn : std_logic := '0';

    -- DUT signals (one per port, using concrete resolved types)
    signal <port_name> : <concrete_type> := <init_value>;
    ...

begin

    -- Clock generation
    CLK_GEN : process
    begin
        clk <= '0';
        wait for CLK_PERIOD / 2;
        clk <= '1';
        wait for CLK_PERIOD / 2;
    end process CLK_GEN;

    -- DUT instantiation
    UUT : <EntityName>
    port map(
        <port_name> => <signal_name>,
        ...
    );

    -- Stimulus
    STIM : process
    begin
        resetn <= '0';
        wait for CLK_PERIOD * 5;
        resetn <= '1';
        wait for CLK_PERIOD * 10;
        -- TODO: add test vectors here
        wait;
    end process STIM;

end architecture <EntityName>_TB_arch;
```

### Rules for signal declarations
- `std_logic` inputs/outputs → initialize to `'0'`
- `std_logic_vector` inputs → initialize to `(others => '0')`
- `std_logic_vector` outputs → initialize to `(others => '0')`
- Do **not** include a `generic map` in the UUT instantiation — this is a synthesis-style TB with no generics
- Do **not** use `use work.<EntityName>` — use the explicit `component` declaration above

## Step 6 — Write the file

Write the generated testbench to the Simulation folder path determined in Step 4.
Report the full output path to the user when done.
