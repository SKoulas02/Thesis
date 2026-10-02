# Algorithm-Hardware Co-design for Deep Learning Applications

Diploma thesis — School of Electrical and Computer Engineering, National Technical University
of Athens (ECE/NTUA).

This repository contains a **bfloat16 GEMV (matrix–vector multiplication) accelerator with
runtime-selectable 2:M structured sparsity** (2:4, 2:8, 2:16, 2:32) for the **Xilinx Alveo
U280** with HBM, and a **dense baseline** built from the same components so that the two can
be compared on equal terms. It holds the VHDL, the Vitis system integration (HLS data movers,
OpenCL hosts, link configurations), the verification flow (Python golden model and
testbenches), the scaling study over six engine sizes, the multi-tenant study (several
independent engines on one card) and all measurement data.

---

## Contents

- [Algorithm-Hardware Co-design for Deep Learning Applications](#algorithm-hardware-co-design-for-deep-learning-applications)
  - [Contents](#contents)
  - [The design in brief](#the-design-in-brief)
  - [Results at a glance](#results-at-a-glance)
  - [Repository layout](#repository-layout)
    - [`GEMV_4.0_Source/` — the sparse engine](#gemv_40_source--the-sparse-engine)
    - [`GEMV_Dense_Source/` — the dense baseline](#gemv_dense_source--the-dense-baseline)
    - [`GEMV_4.0/`, `GEMV_Dense/` — Vivado projects](#gemv_40-gemv_dense--vivado-projects)
    - [`Vitis/` — system integration](#vitis--system-integration)
    - [`family_builds/`](#family_builds)
    - [`multi_tenant/` — several engines on one card](#multi_tenant--several-engines-on-one-card)
    - [`workload/` — one workload on every architecture](#workload--one-workload-on-every-architecture)
    - [`reports/`](#reports)
    - [`results/`](#results)
    - [`scripts/`](#scripts)
  - [Requirements](#requirements)
  - [After cloning: paths to edit](#after-cloning-paths-to-edit)
  - [How to run](#how-to-run)
    - [1. RTL simulation (no FPGA needed)](#1-rtl-simulation-no-fpga-needed)
    - [2. Out-of-context synthesis and implementation](#2-out-of-context-synthesis-and-implementation)
    - [3. Full system on the Alveo U280](#3-full-system-on-the-alveo-u280)
    - [4. Multi-tenant builds](#4-multi-tenant-builds)
    - [5. Measurements and result workbooks](#5-measurements-and-result-workbooks)
    - [6. Workload study](#6-workload-study)
  - [Notes](#notes)

---

## The design in brief

- **Compute.** 8 C Cores × 8 C Blocks. Each C Block owns one output row and contains 2 bf16
  multipliers, 1 adder and 1 accumulator, so 64 rows are in flight and 128 MACs are done per
  cycle. Blocks are independent: there is no reduction tree.
- **2:M sparsity.** Every group of M weights holds exactly 2 non-zeros, stored with 5-bit
  indices. Each C Block uses its indices to pick the matching activations out of a shared
  32-element activation window. The window is held for 32/M cycles (8 / 4 / 2 / 1 for
  2:4 / 2:8 / 2:16 / 2:32), and that is the only thing that changes between modes, so every
  multiplier stays busy at every sparsity.
- **Runtime reconfiguration.** The 2-bit sparsity code travels inside the index stream, so
  the mode can change between groups of rows within one calculation, with no reload and no
  host involvement.
- **Activation reuse.** The activation vector (up to 8,192 elements) is read from HBM once
  into an on-chip replay buffer and recirculated for every group of output rows.
- **Memory.** 17 HBM pseudo-channels: 8 weights, 3 indices, 2 activations, 4 outputs. The
  dense baseline uses 14 (no index channels).
- **System.** The engine is a free-running Vitis RTL kernel with AXI4-Stream ports only.
  HLS data movers, one per pseudo-channel, stream data between HBM and the engine:

  ```
  sparse:  HBM ─► 13 × krnl_mm2s ─► krnl_gemv_sparse ─► 4 × krnl_s2mm ─► HBM
  dense:   HBM ─► 10 × krnl_mm2s ─► krnl_gemv_dense  ─► 4 × krnl_s2mm ─► HBM
  ```

- **Dense baseline.** The same design with sparsity support removed. Shared VHDL files are
  byte-identical copies and both projects use identical IP settings, so the difference
  between the two isolates what sparsity costs and what it buys.
- **Scaling.** The engine is parameterised by the number of cores and blocks. Six
  configurations, from 4×4 to 4×32 and including 8×8, were built and measured on the card
  (see `family_builds/`).
- **Multi-tenancy.** The same engine can be placed several times in one bitstream, each copy
  with its own matrix, sparsity mode, activation vector and HBM channels. Up to five 4×4
  engines, three 8×4 or two 16×3 were built and measured (see `multi_tenant/`).

Both designs produce bit-exact results against the Python golden model, in RTL simulation
and on the U280 at 300 MHz and 325 MHz. The six family configurations and every measured
multi-tenant build are bit-exact on the card at their own clocks.

---

## Results at a glance

Every single-engine and multi-tenant build measured on the U280, sorted by the number of HBM
pseudo-channels it uses. Throughput is steady state (launch overhead removed) in actual
multiply-accumulates per second; energy per MAC is the whole board's power, static and
dynamic, divided by that throughput. Single engines run the MIXED matrix (four row quarters
at 2:4, 2:8, 2:16 and 2:32). In multi-tenant builds each tenant runs its own matrix, all at
the same time, and the tenants use different sparsity modes (2:16, 2:4, 2:32, 2:8, then 2:16
again for a fifth tenant). The clock is the one the card runs the bitstream at. Source:
`results/GEMV_Configurations.csv`.

| Build | Kind | HBM channels | Clock (MHz) | Throughput (GMAC/s) | Energy (nJ/MAC) |
|---|---|---|---|---|---|
| 4x4 | single engine | 6 | 400 | 12.6 | 2.17 |
| 8x4 | single engine | 10 | 375 | 23.7 | 1.26 |
| 2 × 4x4 | multi-tenant | 12 | 394 | 25.0 | 1.23 |
| 16x3 | single engine | 13 | 350 | 33.1 | 0.97 |
| 8x8 | single engine | 17 | 325 | 41.1 | 0.88 |
| 3 × 4x4 | multi-tenant | 18 | 399 | 37.9 | 0.98 |
| 2 × 8x4 | multi-tenant | 20 | 361 | 45.6 | 0.84 |
| 4x24 | single engine | 24 | 300 | 57.0 | 0.71 |
| 4 × 4x4 | multi-tenant | 24 | 374 | 47.4 | 0.85 |
| 2 × 16x3 | multi-tenant | 26 | 341 | 64.6 | 0.66 |
| 5 × 4x4 | multi-tenant | 30 | 329 | 51.7 | 0.82 |
| 3 × 8x4 | multi-tenant | 30 | 354 | 66.8 | 0.65 |
| 4x32 | single engine | 32 | 250 | 63.6 | 0.66 |

- **A single engine's clock falls as the engine grows**, from 400 MHz (4x4) to 250 MHz
  (4x32). Several smaller engines holding the same number of multipliers keep a higher clock,
  so at every equal multiplier count the multi-tenant build is 5–17% faster, with an energy
  per MAC that is lower or within 1%. The cost is HBM channels: every engine needs its own two
  activation channels.
- **3 × 8x4 is the fastest build on the card**, ahead of the largest single engine (4x32)
  while using 30 channels instead of 32.
- **Tenants do not measurably slow each other.** Across all multi-tenant runs, every
  tenant's run time with its neighbours running is within 1.5% of its run time alone, while
  the tenants overlap for 83–97% of the run.
- **Idle power follows the HBM channels in use.** It rises with the number of channels and
  steps up by about 3 W between the builds that stop at channel 12 and those that reach
  channel 16, where the U280's second HBM stack begins (27.6 W for 16x3 on 13 channels,
  30.8 W for 8x8 on 17).
- **With launches included, the replicas finish a mixed job first.** In the workload study
  (`workload/`), every build ran the same 132 matrices (195.6 M MACs), seven calculations per
  engine. The five fastest builds per pass are all multi-tenant, at 6.8–7.0 ms. The best
  single engines (8x8, 4x24) take 8.2 ms, and the 4x32 takes 9.65 ms. Launch and completion
  overhead is 10–68% of the wall time, and it grows with the compute units that one host
  thread launches; each tenant launches only its own. Source: `results/GEMV_Workload.csv`.

---

## Repository layout

```
.
├── GEMV_4.0_Source/         sparse engine: VHDL, testbenches, golden model
├── GEMV_4.0/                Vivado 2022.2 project for the sparse engine
├── GEMV_Dense_Source/       dense baseline: VHDL, testbenches, golden model
├── GEMV_Dense/              Vivado 2022.2 project for the dense baseline
├── Vitis/                   system integration: kernel packaging, HLS movers, hosts,
│                            link configs, measurement scripts
├── family_builds/           build files for the six CORES × BLOCKS configurations + their generator
├── multi_tenant/            several independent engines on one card: generator, host,
│                            test and measurement scripts, and the build configurations
├── workload/                one mixed workload (132 matrices) run on all 13 architectures
├── scripts/
│   ├── vivado/              Vivado Tcl: project re-creation, out-of-context sweeps
│   └── analysis/            Python scripts that build the CSVs and workbooks in results/
├── reports/                 Vivado and Vitis reports (utilization, timing, power)
├── results/                 all measurement CSVs and the Excel workbooks built from them
└── au280/                   local copy of the U280 board files (not used by the projects)
```

Every command in this README is run from the repository root unless it says otherwise.

### `GEMV_4.0_Source/` — the sparse engine

| Path | Contents |
|---|---|
| `Design/top_module_2N.vhd` | `two2N`, the engine top: ingress FIFOs, sparsity control, 8 C Cores, output FIFO |
| `Design/c_core_4.0.vhd`, `Design/c_block_4.0.vhd` | C Core (8 C Blocks + activation window) and C Block (index gather, 2 multipliers, adder, accumulator) |
| `Design/weights_fifo_4.0_2k.vhd` | weight and index ingress: 11 per-channel AXI-Stream FIFOs joined into one beat |
| `Design/vector_fifo_4.0.vhd`, `Design/vector_fifo_cycle_4.0.vhd` | activation ingress and the activation replay buffer |
| `Design/c_fifo_4.0.vhd` | output FIFO, split over 4 output channels |
| `Design/*_wrapper_4.0.vhd` | wrappers around the bf16 Floating-Point IP (multiplier, adder, accumulator) |
| `Design/two2N_axis.vhd`, `Design/krnl_gemv_sparse.v` | AXI4-Stream wrapper (17 separate stream interfaces) and the Vitis RTL-kernel top |
| `Simulation/` | unit testbenches for each block; `two2N_TB.vhd`, the file-driven full-engine testbench with random input gaps and output back-pressure; testbenches for the AXI-Stream wrapper, including two back-to-back calculations |
| `Emulation/gemv4_cosim_gen.py` | stimulus generator and bit-accurate Python golden model: writes `weights.hex`, `indices.hex`, `activations.hex`, `golden.txt` |
| `Emulation/compare_gemv4.py` | compares `output.txt` (from simulation or hardware) with `golden.txt`; `compare_gemv4_py36.py` is the Python 3.6 version |
| `Emulation/hex_to_bin.py` | packs the `.hex` stimulus into one `bin/*.bin` image per HBM channel for the host |
| `Emulation/gen_timing_stimulus.py` | large stimulus for throughput runs (writes no golden output) |
| `timing.xdc` | clock constraint — see [section 2](#2-out-of-context-synthesis-and-implementation) |

### `GEMV_Dense_Source/` — the dense baseline

Same layout as the sparse engine: `c_block_dense.vhd`, `c_core_dense.vhd`,
`weights_fifo_dense.vhd`, `top_module_dense.vhd` (top `dense_gemv`), the wrapper
`dense_gemv_axis.vhd` and kernel top `krnl_gemv_dense.v`; generator `gemv_dense_gen.py`,
comparison `compare_dense.py`. [`GEMV_Dense_Source/README.md`](GEMV_Dense_Source/README.md)
lists exactly what differs from the sparse design and gives the verification ladder.

### `GEMV_4.0/`, `GEMV_Dense/` — Vivado projects

Vivado 2022.2 project files (`.xpr`) and IP configurations (`.xci`). The `.xci` files are the
only record of the non-default IP settings (First-Word-Fall-Through FIFOs, depths,
`prog_full`, accumulator precision), and they are identical in the two projects. Each project
refers to its sources as `../<Source folder>/…`, so **a project folder must stay next to its
source folder**.

### `Vitis/` — system integration

| File(s) | Purpose |
|---|---|
| `gen_xo_sparse.tcl`, `gen_xo_dense.tcl` | package an engine as a Vitis RTL kernel (`.xo`) through the RTL Kernel Wizard |
| `krnl_mm2s.cpp`, `krnl_s2mm.cpp` | HLS data movers: HBM → AXI4-Stream and AXI4-Stream → HBM, one compute unit per pseudo-channel, shared by both designs |
| `tb_mm2s.cpp`, `tb_s2mm.cpp`, `run_hls_movers.tcl` | HLS C simulation, synthesis and co-simulation of the movers |
| `host.cpp`, `host_sparse.cpp` | OpenCL host programs (dense, sparse): load the `.bin` images, run every compute unit, time the run with OpenCL profiling events, write `output.txt` |
| `dense_hbm.cfg`, `sparse_hbm.cfg` | `v++` link configurations: compute units, HBM channel binding, kernel-to-kernel streams |
| `dense_ddr.cfg`, `sparse_ddr.cfg` | the same systems bound to DDR4 instead of HBM |
| `impl_opt.cfg`, `impl_opt_perf.cfg`, `impl_opt_perf350.cfg` | synthesis/implementation strategies used for the 300, 325 and 350 MHz builds |
| `impl_family.cfg` | the one synthesis/implementation strategy shared by all six family builds and all multi-tenant builds |
| `slr_floorplan.cfg`, `slr_floorplan_dense.cfg` | die (SLR) assignment: engine in SLR1, movers in SLR0 next to the HBM |
| `profile.cfg`, `xrt.ini` | hardware profiling monitors (for a separate build, never built) and XRT runtime profiling settings |
| `run_ladder.sh` | hardware correctness run: each sparsity, plus one calculation that switches sparsity between groups of rows, each checked against the golden model |
| `measure.py`, `measure_sparse.py`, `run_avg3.py`, `run_shapes.py` | throughput measurement: problem-size sweeps, repeated runs, 11 matrix shapes |
| `measure_power.py`, `power_scraper.py` | power measurement from the card's telemetry (`xbutil`) during a sustained run |
| `run_family_measure.py` | full measurement campaign (throughput, power, shapes) for one family configuration |
| `build_floorplan.sh`, `build_diagonal_parallel.sh` | batch link scripts: keep the routed checkpoint for floorplan pictures; link several configurations in parallel |

### `family_builds/`

Build files for six CORES × BLOCKS configurations (4×4, 8×4, 16×3, 8×8, 4×24, 4×32): a
wrapper and kernel top with the right number of streams, a `gen_xo` script, link and
floorplan configurations, and the host constants. Each configuration has a `BUILD.md` with
step-by-step instructions, and [`family_builds/README.md`](family_builds/README.md)
describes the campaign and its outcome. All six use the link strategy in
`Vitis/impl_family.cfg`.

`family_builds/make_family_build.py` generates the six configuration folders and
`Vitis/impl_family.cfg`. It uses `Vitis/gen_xo_sparse.tcl` as the template for the
`gen_xo` scripts:

```bash
python family_builds/make_family_build.py          # all six
python family_builds/make_family_build.py 4x4      # just one
```

### `multi_tenant/` — several engines on one card

N copies of one family engine in a single bitstream. Each copy (a *tenant*) has its own HBM
channels, its own data movers, its own matrix, sparsity mode and activation vector, and its
own command queue on the host. Nothing is shared except the card, its HBM and the kernel
clock. No RTL changes: the family engine's `.xo` is instantiated N times.
[`multi_tenant/README.md`](multi_tenant/README.md) describes the study, the builds and the
findings.

| File(s) | Purpose |
|---|---|
| `make_multi_build.py` | generates one build folder: link configuration, both floorplans and a `BUILD.md`, for any family shape and tenant count |
| `x2/`, `x3/`, `x4/`, `x5/` | 2 to 5 tenants of 4×4 (`x3/start_build.sh` starts that link with one command) |
| `8x4_x2/`, `8x4_x3/`, `16x3_x2/` | 2 and 3 tenants of 8×4, 2 tenants of 16×3 |
| `host_sparse_multi.cpp` | OpenCL host for N tenants: one command queue per tenant, per-tenant timing, a power soak with one thread per tenant; the engine shape is set at compile time (`-DCORES=8 -DBLOCKS=4`) |
| `prep_tenants.py` | stimulus for every tenant, each with a different matrix: small matrices with golden outputs for correctness, or large ones for timing |
| `run_multi_measure.py` | interference and throughput (each tenant alone, then 2, 3 … N together) and a power soak at each number of running tenants |
| `run_card_tests.sh` | every card test for one bitstream, in order, stopping at the first failure: correctness, endurance, probe under load, both measurements |
| `prep_shared.py`, `run_shared.py` | shared-workload experiment: four 4×4 tenants split one matrix, one sparsity mode each |

### `workload/` — one workload on every architecture

The same 132 matrices (the eleven shapes of the shape sweep × the four sparsities × 3 copies)
run on all 13 architectures: the six family engines and the seven multi-tenant builds. On a
multi-tenant build, the tenants run different matrices at the same time, assigned before
runtime so that every tenant has the same predicted time. The study reports the time and the
energy of the whole workload, both with launch overhead included and with it removed.
[`workload/README.md`](workload/README.md) describes the workload, the plan and what is
measured.

| File(s) | Purpose |
|---|---|
| `plan_workload.py` | the workload, the 13 architectures, the assignment of matrices to tenants and the HBM capacity check; writes `plans/<arch>.json` |
| `host_workload.cpp` | OpenCL host: every tenant runs a list of calculations in its own thread, all tenants at the same time; also drives the single-engine bitstreams |
| `run_workload.py` | one architecture: a golden-checked correctness gate, or the measurement (timed passes, power soak, CSVs) |
| `run_all_workloads.sh` | runs all 13 in turn, gate then measurement, stopping at the first failure |

### `reports/`

| Folder | Contents |
|---|---|
| `ooc/` | out-of-context reports of the engines: `two2N_axis_utilization_placed.rpt` and `dense_gemv_axis_utilization_placed.rpt` are the inputs of `make_area_xlsx.py`; `two2N_*` are utilization, timing and power of the bare sparse engine |
| `dense_reports_300/`, `sparse_reports_300slr/` | utilization (whole design, per kernel, per SLR) and timing reports from the Vitis link of the measured 300 MHz systems; `impl_1_kernel_util_routed.rpt` breaks utilization down per compute unit, movers included |
| `family_hw_reports/` | the same reports for the family builds and for the earlier 8×8 sparse builds (225, 300 and 325 MHz); `reports_350/` is the dense 350 MHz build. `family_hw_reports.tar.gz` beside it is a compressed copy |
| `reports_multi_tenant.tar.gz` | the same reports for the nine multi-tenant bitstreams (one folder per build, 508 MB unpacked): `tar xzf reports/reports_multi_tenant.tar.gz -C reports` |
| `diagonal_sweep/`, `family_sweep/` | out-of-context utilization and timing reports of the bare engine for each CORES × BLOCKS point, plus a summary CSV; produced by `scripts/vivado/sweep_diagonal_ooc.tcl` and `sweep_families_ooc.tcl` |

### `results/`

| Files | Contents |
|---|---|
| `dense_*.csv`, `sparse_*.csv`, `*_avg3_*.csv`, `shapes_*.csv`, `power_*.csv`, `mixed_sparsity_300MHz.csv` | raw hardware measurements of the 8×8 dense and sparse systems |
| `family_measurements/<config>/` | raw hardware measurements of each family configuration |
| `multi_tenant/` | raw hardware measurements of the multi-tenant builds, one CSV per bitstream and workload, named `multi_[<shape>_]x<N>_<MHz>MHz[_all2to32].csv` (no shape = 4×4); `shared_x4_374MHz_during_x5_build.csv` is the shared-workload experiment |
| `workload/` | raw hardware measurements of the workload study, per build: `workload_<arch>_<MHz>MHz.csv` (summary) and `..._calcs.csv` (every calculation of every timed pass) |
| `diagonal_summary_run1.csv`, `family_summary_all18.csv` | summaries of the two out-of-context sweeps |
| `GEMV_*.csv` | chart-ready tables built by the `make_*_csv.py` scripts in `scripts/analysis/` |
| `GEMV_Hardware_Results.xlsx` | built by `make_comparison_xlsx.py` |
| `GEMV_Chart_Data.xlsx` | built by `make_chart_xlsx.py` (rebuilt from scratch on every run) |
| `GEMV_Area_Comparison.xlsx` | built by `make_area_xlsx.py` |
| `GEMV_Charts_Final.xlsx` | the final charts, made by hand; no script writes this file |
| `Utilization.xlsx` | per-module utilization tables of the engines |

### `scripts/`

| File(s) | Purpose |
|---|---|
| `vivado/create_sparse_2021.tcl`, `vivado/create_dense_2021.tcl` | recreate a Vivado project, including all IP, from scratch on Vivado 2021.1; optionally run OOC synthesis and implementation. They find the source folders two levels above themselves, so **keep them in `scripts/vivado/`** |
| `vivado/sweep_diagonal_ooc.tcl`, `vivado/sweep_families_ooc.tcl` | out-of-context area/Fmax sweeps over CORES × BLOCKS; run them from `reports/` so their output lands in `reports/diagonal_sweep/` and `reports/family_sweep/`. The family sweep constrains the bare engine at 1.5 ns: set the `clk` line of `timing.xdc` live, with that period, while it runs (see the script's header) |
| `vivado/s7_wizard_probe.tcl` | probe of the RTL Kernel Wizard options in Vivado 2021.1 (kept for reference) |
| `analysis/make_*_csv.py`, `analysis/make_*_xlsx.py`, `analysis/make_serpens_*.py` | turn raw measurements and reports into the CSVs and workbooks in `results/`; each script's docstring names its inputs and outputs |
| `analysis/check_family_measure.py` | checks one family configuration's measurements and compares it with the others |

The analysis scripts find `results/` and `reports/` two levels above themselves, so they
work from any current directory. Some import each other, so keep them together in
`scripts/analysis/`. `make_multi_tenant_csv.py` also imports `multi_tenant/prep_tenants.py`
and `make_workload_csv.py` imports `workload/plan_workload.py`, so keep those folders at the
repository root.

---

## Requirements

| Tool | Used for | Version used |
|---|---|---|
| Vivado | RTL simulation and OOC synthesis with the `.xpr` projects | 2022.2 |
| Vivado + Vitis + Vitis HLS | recreating the projects with `scripts/vivado/create_*_2021.tcl`, and the whole U280 system build | 2021.1 |
| XRT | running on the card | 2.13 (2022.1 branch) |
| Vitis platform | U280 target; the card must be flashed with the matching shell | `xilinx_u280_xdma_201920_3` |
| Python 3 | stimulus, comparison, measurement, result building | scripts under `Vitis/`, `multi_tenant/` and `workload/` and the `_py36` files run on 3.6; `compare_gemv4.py` and `compare_dense.py` need 3.9+; the `make_*_xlsx.py` scripts need `openpyxl` |
| g++ | host programs | C++14 |

Implementation for the U280 (XCU280) needs tens of GB of RAM. The hardware builds for this
thesis ran on a Linux server (Ubuntu 18.04, 192 GB RAM) with the U280 installed. One `v++`
hardware link takes several hours: 3–5 hours for the 8×8 designs, up to about 7.5 hours for
the multi-tenant builds.

---

## After cloning: paths to edit

The scripts were written for specific machines and contain absolute paths. XSim runs
testbenches from its own run directory, so testbench paths must be absolute.

| File(s) | Hard-coded path | Change to |
|---|---|---|
| `GEMV_4.0_Source/Simulation/two2N_TB.vhd` | `C:\Koulas\ECE\Thesis\Code\…` in `WPATH`, `IPATH`, `APATH`, `OPATH`, `TPATH` | your clone's `GEMV_4.0_Source/Emulation/` |
| `GEMV_Dense_Source/Simulation/dense_TB.vhd` | `C:\Koulas\ECE\Thesis\Code\…` in `WPATH`, `APATH`, `OPATH`, `TPATH` | your clone's `GEMV_Dense_Source/Emulation/` |
| the other `*_axis_TB.vhd`, `*_rerun_TB.vhd`, `*_conc_TB.vhd` testbenches | `/home/skoulas/GEMV_Sparse/…`, `/home/skoulas/GEMV_Dense/…` | the matching `Emulation/` folder |
| `Vitis/gen_xo_sparse.tcl`, `Vitis/gen_xo_dense.tcl`, `family_builds/*/gen_xo_*.tcl` | `set repo "/home/skoulas/GEMV_Sparse"` (or `GEMV_Dense`) | your clone's root folder |
| `Vitis/tb_mm2s.cpp` | `#define EMU_DIR` | your clone's `GEMV_Dense_Source/Emulation` |
| `Vitis/run_ladder.sh`, `Vitis/build_floorplan.sh`, `Vitis/build_diagonal_parallel.sh`, `Vitis/run_family_measure.py`, and the usage examples of the measurement scripts | `$HOME/GEMV_Sparse`, `~/GEMV_Dense`, platform path | your paths |
| `multi_tenant/run_card_tests.sh` | `$HOME/GEMV_Sparse/GEMV_4.0_Source/Emulation` (or set `EMU`), `$HOME/GEMV_Sparse` for `power_scraper.py` and the report copies, the server name in the printed `scp` line | your clone and server |
| `workload/plan_workload.py`, `workload/run_workload.py`, `workload/run_all_workloads.sh` | the build folder and `.xclbin` name of each architecture (`ARCHS`), `~/GEMV_Sparse` (or set `GEMV_ROOT`), `…/GEMV_4.0_Source/Emulation` (or set `EMU`), the server name in the printed `scp` line | your builds, clone and server |
| the `BUILD.md` files written by `multi_tenant/make_multi_build.py`, and `multi_tenant/x3/start_build.sh` | `~/GEMV_Sparse/…`, the server name, the install paths under `/opt` | your paths |

---

## How to run

### 1. RTL simulation (no FPGA needed)

**Sparse engine**

1. Generate a test case and its expected output (from the repository root):

   ```bash
   cd GEMV_4.0_Source/Emulation
   python gemv4_cosim_gen.py --sparsity 00 --nwin 2 --nlaps 2
   ```

   | Option | Meaning |
   |---|---|
   | `--sparsity` | `00` = 2:4, `01` = 2:8, `10` = 2:16, `11` = 2:32 |
   | `--nwin` | number of 32-element windows, so the vector length is 32 × `nwin` |
   | `--nlaps` | number of 64-row groups that reuse the same vector (≥ 2 exercises the replay buffer) |
   | `--sparsities 00,01,10,11` | a different sparsity for each group of rows (runtime reconfiguration) |
   | `--value-hi 100` | larger values, to exercise bf16 rounding |

2. Set the five path constants in `GEMV_4.0_Source/Simulation/two2N_TB.vhd` (see
   [paths to edit](#after-cloning-paths-to-edit)).
3. Open `GEMV_4.0/GEMV_4.0.xpr` in Vivado 2022.2 and run **Run Simulation → Run Behavioral
   Simulation** (simulation top: `two2N_TB`). If Vivado reports missing IP output products,
   select the IP in the Sources window and run **Generate Output Products**.
4. Compare with the golden model:

   ```bash
   python compare_gemv4.py
   ```

   Expected: `DATA  : PASS (bit-exact)` and `TLAST : PASS`.

After editing a VHDL file, use **Relaunch Simulation**, not *Restart*, so the sources are
recompiled.

**Dense baseline:** the same steps in `GEMV_Dense_Source/`: `python gemv_dense_gen.py --nwin 2
--nlaps 2`, the four path constants in `dense_TB.vhd`, project `GEMV_Dense/GEMV_Dense.xpr`
(simulation top `dense_TB`), then `python compare_dense.py`. The full verification ladder is
in [`GEMV_Dense_Source/README.md`](GEMV_Dense_Source/README.md).

**Other Vivado versions:** the `.xpr` projects are Vivado 2022.2 projects. For Vivado 2021.1,
recreate them with `scripts/vivado/create_sparse_2021.tcl` / `create_dense_2021.tcl`
(section 2).

### 2. Out-of-context synthesis and implementation

This gives the engine's utilization and module-level Fmax. Synthesis must be
**out-of-context**: the engines have thousands of top-level ports, which no package can
bond.

1. **Select the right clock line in `timing.xdc`.** Each `timing.xdc` has two
   `create_clock` lines, one commented out. The repository default constrains `ap_clk`,
   the clock of the AXI-Stream wrapper (`two2N_axis` / `dense_gemv_axis`) and of the kernel
   flow. The `.xpr` projects and the `create_*_2021.tcl` scripts synthesise the bare engine
   (`two2N` / `dense_gemv`), whose clock port is `clk`. For those, comment out the `ap_clk`
   line and uncomment the `clk` line. Constraining a port that does not exist is only a
   critical warning, and the run then reports **`WNS = inf`**, which looks like a pass and
   is not one.
2. Run synthesis and implementation, in either of two ways:
   - **Vivado 2022.2:** open the `.xpr` and run implementation. The sparse project already
     sets `-mode out_of_context` (Settings → Synthesis → More Options). The dense project
     does not, so add it there first.
   - **Vivado 2021.1:** from the repository root,

     ```bash
     vivado -mode batch -source scripts/vivado/create_sparse_2021.tcl -tclargs impl
     vivado -mode batch -source scripts/vivado/create_dense_2021.tcl  -tclargs impl
     ```

     The scripts create the project, generate all IP with the same settings for both
     designs, and write reports to `<project>/reports/`. Without `impl`, they only create
     the project. The scripts refuse to overwrite an existing project, and `GEMV_4.0/` and
     `GEMV_Dense/` are part of the repository, so first change `proj_name` near the top of
     the script (for example to `GEMV_4.0_2021`).
3. Restore the `ap_clk` line in `timing.xdc` afterwards.

The constraint is 2.222 ns (450 MHz), so Fmax = 1000 / (2.222 − WNS) MHz. A design that
meets its constraint reports only a lower bound on Fmax; that is why the family sweep
(`scripts/vivado/sweep_families_ooc.tcl`) uses 1.5 ns instead.

### 3. Full system on the Alveo U280

This needs a Linux machine with Vitis 2021.1, XRT, the `xilinx_u280_xdma_201920_3` platform
and a U280 flashed with that shell. The commands below build the 8×8 sparse system. Dense is
the same with the names in brackets. `<clone>` is the path of your clone. Each family
configuration has its own `family_builds/<config>/BUILD.md`.

**Step 0 — environment** (every new shell; install paths as on the build server):

```bash
source /opt/Xilinx/Vitis/2021.1/settings64.sh     # Vitis, Vitis HLS, Vivado
source /opt/xilinx/xrt/setup.sh                    # XRT
export PLATFORM=/opt/xilinx/platforms/xilinx_u280_xdma_201920_3/xilinx_u280_xdma_201920_3.xpfm
```

Always pass the absolute `.xpfm`. If a newer U280 platform is installed next to it, Vitis
2021.1 cannot parse it, and platform discovery fails with `failed to parse the XPFM file`.
Source both scripts: `xbutil`, which the power measurements need, is only on the `PATH` after
`/opt/xilinx/xrt/setup.sh`.

**Step 1 — package the engine as an RTL kernel.** Set `repo` in `Vitis/gen_xo_sparse.tcl`
[`Vitis/gen_xo_dense.tcl`] to your clone's root. Then, in the Vivado 2021.1 Tcl Console
with **no project open**:

```tcl
source <clone>/Vitis/gen_xo_sparse.tcl
```

This writes `<clone>/krnl_gemv_sparse.xo` [`krnl_gemv_dense.xo`], with its build files in
`<clone>/xo_build/`. Check that the engine is actually inside:
`unzip -l krnl_gemv_sparse.xo` must list the engine's `.vhd` files and the 7 `.xci` IP
files. A `.xo` that holds only the wizard's empty shell links without errors and computes
nothing.

**Step 2 — compile the data movers.** Do this once; every design shares them:

```bash
cd <clone>/Vitis
v++ -c -t hw     --platform $PLATFORM -k krnl_mm2s -o krnl_mm2s.hw.xo     krnl_mm2s.cpp
v++ -c -t hw     --platform $PLATFORM -k krnl_s2mm -o krnl_s2mm.hw.xo     krnl_s2mm.cpp
v++ -c -t hw_emu --platform $PLATFORM -k krnl_mm2s -o krnl_mm2s.hw_emu.xo krnl_mm2s.cpp
v++ -c -t hw_emu --platform $PLATFORM -k krnl_s2mm -o krnl_s2mm.hw_emu.xo krnl_s2mm.cpp
```

Optionally, `vitis_hls -f run_hls_movers.tcl` (from `Vitis/`) first runs C simulation,
synthesis and co-simulation of the movers. Set `EMU_DIR` in `tb_mm2s.cpp` first.

**Step 3 — build the host:**

```bash
cd <clone>/Vitis
g++ -Wall -O2 -std=c++14 -I$XILINX_XRT/include host_sparse.cpp -L$XILINX_XRT/lib -lOpenCL -lrt -lstdc++ -pthread -o host_sparse
g++ -Wall -O2 -std=c++14 -I$XILINX_XRT/include host.cpp        -L$XILINX_XRT/lib -lOpenCL -lrt -lstdc++ -pthread -o host
```

The hosts and the stimulus scripts are set up for the 8×8 configuration. For a family
configuration, follow its `BUILD.md`, which lists the constants to change in
`host_sparse.cpp`, `hex_to_bin.py` and `gen_timing_stimulus.py`.

**Step 4 — generate and pack a test case:**

```bash
cd <clone>/GEMV_4.0_Source/Emulation         # [<clone>/GEMV_Dense_Source/Emulation]
python3 gemv4_cosim_gen.py --sparsity 00 --nwin 4 --nlaps 4   # [gemv_dense_gen.py --nwin 4 --nlaps 4]
python3 hex_to_bin.py pack                   # -> bin/*.bin, one image per HBM channel
```

**Step 5 — hardware emulation (recommended).** It takes minutes instead of hours, and it
catches connection mistakes that would otherwise show up only as a hang on the card. Use a
small test case (`--nwin 1 --nlaps 1`) in step 4.

```bash
cd <clone>/Vitis
emconfigutil --platform $PLATFORM --nd 1
export XCL_EMULATION_MODE=hw_emu
v++ -t hw_emu --platform $PLATFORM --config sparse_hbm.cfg -l -o gemv_sparse.hw_emu.xclbin \
    krnl_mm2s.hw_emu.xo ../krnl_gemv_sparse.xo krnl_s2mm.hw_emu.xo
./host_sparse gemv_sparse.hw_emu.xclbin ../GEMV_4.0_Source/Emulation
unset XCL_EMULATION_MODE
```

Then compare as in step 7.

**Step 6 — link for hardware.** This takes hours per design, so run it inside `tmux` and
check free disk space first.

```bash
cd <clone>/Vitis
# sparse, 300 MHz
v++ -t hw --platform $PLATFORM \
    --config sparse_hbm.cfg --config impl_opt.cfg --config slr_floorplan.cfg \
    --kernel_frequency 300 -l -o gemv_sparse.xclbin \
    krnl_mm2s.hw.xo ../krnl_gemv_sparse.xo krnl_s2mm.hw.xo

# dense, 300 MHz
v++ -t hw --platform $PLATFORM --config dense_hbm.cfg \
    --kernel_frequency 300 -l -o gemv_dense.xclbin \
    krnl_mm2s.hw.xo ../krnl_gemv_dense.xo krnl_s2mm.hw.xo
```

- For **325 MHz**, link with `--config <design>_hbm.cfg --config impl_opt_perf.cfg --config
  slr_floorplan.cfg` [`slr_floorplan_dense.cfg`] and `--kernel_frequency 325`.
- `--kernel_frequency` is required: the platform's default kernel clock is 500 MHz, which
  neither design meets.
- **Read the final timing report**, `_x/reports/link/imp/impl_1_*_timing_summary_postroute_physopted.rpt`.
  With the post-route phys-opt strategies, `*_timing_summary_routed.rpt` is the state
  *before* the last optimisation step.
- **The runtime clock is not always the target.** If the kernel clock misses its target,
  `v++` still writes an `.xclbin`, with its kernel clock lowered to the highest whole MHz the
  timing proves: min(target, ⌊1000 / (period − WNS)⌋). Such a bitstream is valid at that
  clock. A build that closes is capped at its target. Read the clock the card will run with
  `xclbinutil --info --input <file>.xclbin | grep -A3 DATA_CLK`. If a *platform* clock
  fails instead, no `.xclbin` is written at all.
- Never run two links in the same folder at the same time: both write to `_x/` and corrupt
  each other.
- A misspelled port name in a `stream_connect` line is only a warning, and it leaves the
  stream unconnected. If emulation or hardware hangs, check those lines first.

**Step 7 — run and verify:**

```bash
xbutil examine                                # the U280 must be listed with shell xilinx_u280_xdma_201920_3
cd <clone>/Vitis
./host_sparse gemv_sparse.xclbin ../GEMV_4.0_Source/Emulation 300
#   [./host gemv_dense.xclbin ../GEMV_Dense_Source/Emulation 300]
cd <clone>/GEMV_4.0_Source/Emulation          # [<clone>/GEMV_Dense_Source/Emulation]
rm -f tlast.txt                               # left over from simulation; the host cannot see TLAST
python3 compare_gemv4_py36.py                 # [compare_dense_py36.py]
```

Expected: `DATA  : PASS (bit-exact)`. A note that `tlast.txt` was not found is normal on
hardware: TLAST can only be checked in simulation.

The host's third argument is the kernel clock the card runs the `.xclbin` at (its
`DATA_CLK`, see step 6). It only scales the throughput figures the host prints, but a wrong
value mis-scales them silently. An optional fourth argument keeps the card busy for that many
seconds, which is used for power measurement. The host picks the first card whose name
contains "u280". `Vitis/run_ladder.sh` automates steps 4 and 7 for all four sparsities plus a
runtime-reconfiguration run (edit the paths at its top first).

If a run hangs, stop the host and reset the card before running anything else
(`xbutil reset --device <BDF>`); until then, XRT refuses to load any `.xclbin`.

### 4. Multi-tenant builds

N copies of one family engine on one card. The full description is in
[`multi_tenant/README.md`](multi_tenant/README.md); in short:

1. **Generate the build folder.** For example, three tenants of 8×4:

   ```bash
   python multi_tenant/make_multi_build.py --tenants 3 --config 8x4 --clock 375
   ```

   This writes `multi_tenant/8x4_x3/`: the link configuration, both floorplans and a
   `BUILD.md`. The engines move to SLR1 automatically once one die would need more than 85%
   of SLR0's block RAM; `--floorplan split` forces it.
2. **Link** as in that `BUILD.md`. It needs the family engine's `.xo`
   (`family_builds/<shape>/gen_xo_<shape>.tcl` writes `krnl_gemv_sparse_<shape>.xo` to the
   clone's root), the mover `.xo` files from section 3 step 2, and `Vitis/impl_family.cfg`.
3. **Test on the card.** Put `run_card_tests.sh`, `host_sparse_multi.cpp`, `prep_tenants.py`
   and `run_multi_measure.py` in one folder on the server, put `Vitis/power_scraper.py` in the
   build folder, and run from the build folder:

   ```bash
   bash <tools folder>/run_card_tests.sh sparse_8x4_x3_375.xclbin 8 4 3
   ```

   The arguments are the `.xclbin`, the engine's cores and blocks, and the number of tenants.
   The script reads the runtime clock from the `.xclbin`, builds the host for that shape,
   checks every tenant bit-exact (all together, then after 30 s of back-to-back runs, then
   each one while the others run at full rate), and measures interference, throughput and
   power, writing `multi_[<shape>_]x<N>_<MHz>MHz.csv` and `..._all2to32.csv`. It stops at the
   first failure. It refuses to start if the card's power cannot be read, and
   `--mixed-only` repeats only the main measurement.
4. **Build the chart data:** copy the CSVs to `results/multi_tenant/`, then run the
   commands in section 5.

### 5. Measurements and result workbooks

- **Throughput:** `Vitis/measure.py` (dense), `Vitis/measure_sparse.py`, `Vitis/run_avg3.py`,
  `Vitis/run_shapes.py`. **Power:** `Vitis/measure_power.py`. Each script's docstring gives
  its usage. They need only the Python standard library and run on Python 3.6.
  `Vitis/measure_power.py` is for single-engine hosts only; for multi-tenant builds use
  `multi_tenant/run_multi_measure.py`.
- **Chart-ready tables and workbooks:** the `make_*_csv.py` scripts in `scripts/analysis/`
  build the `results/GEMV_*.csv` tables; each script's docstring names its inputs. The
  workbook scripts need `openpyxl`, and Excel must be closed:

  ```bash
  python scripts/analysis/make_multi_tenant_csv.py    # -> the multi-tenant GEMV_*.csv tables
  python scripts/analysis/make_comparison_xlsx.py     # -> results/GEMV_Hardware_Results.xlsx
  python scripts/analysis/make_chart_xlsx.py          # -> results/GEMV_Chart_Data.xlsx
  python scripts/analysis/make_area_xlsx.py           # -> results/GEMV_Area_Comparison.xlsx
  ```

  `GEMV_Chart_Data.xlsx` is rebuilt from scratch, so any chart drawn inside it is lost on the
  next run. The final charts are in `GEMV_Charts_Final.xlsx`, which no script writes.

### 6. Workload study

The same 132 matrices on all 13 architectures; see
[`workload/README.md`](workload/README.md). Every bitstream must already be built, each in
its own folder under `~/GEMV_Sparse` (the folder and `.xclbin` names are in `ARCHS` in
`workload/plan_workload.py`).

1. **Check the plan** (optional, runs anywhere): `python workload/plan_workload.py` prints
   every architecture's predicted time and writes `workload/plans/`.
2. **Copy the four tools to one folder on the server** (`plan_workload.py`,
   `host_workload.cpp`, `run_workload.py`, `run_all_workloads.sh`).
3. **Run the chain** on the server, inside `tmux`, with nothing else using the card:

   ```bash
   bash <tools folder>/run_all_workloads.sh              # all 13
   bash <tools folder>/run_all_workloads.sh 3x8x4 4x32   # only these
   bash <tools folder>/run_all_workloads.sh --check      # preflight only
   ```

   It first checks every selected architecture: its `.xclbin` exists and runs at the clock
   the plan expects. Then, for each one, it builds the host for the engine shape, and runs
   the correctness gate (three golden-checked calculations per tenant) and the measurement
   (3 timed passes, a 60 s power soak and 20 s of idle power). It stops at the first failure
   and says how to resume.
4. **Copy the CSVs home** with the `scp` line the chain prints last, into `results/workload/`.
5. **Build the chart data** (Excel closed):

   ```bash
   python scripts/analysis/make_workload_csv.py    # -> results/GEMV_Workload.csv
   python scripts/analysis/make_chart_xlsx.py      # -> its "Workload" sheet
   ```

   The first checks all 13 runs against the plan and writes nothing if one fails. Wall time
   and energy are measured; engine time (launching free) is derived from each build's
   steady-state efficiency.

---

## Notes

- The thesis text is not part of this repository.
- The shared VHDL files in `GEMV_Dense_Source/Design/` are copies of the sparse originals,
  not references. If one side is edited, re-sync the pair (see
  [`GEMV_Dense_Source/README.md`](GEMV_Dense_Source/README.md)), or the comparison stops
  isolating sparsity.
- The stimulus scripts in `GEMV_4.0_Source/Emulation/` are shared by every build. The packing
  constants at the top of `hex_to_bin.py` must match the engine being tested;
  `multi_tenant/prep_tenants.py`, `multi_tenant/run_card_tests.sh` and
  `workload/run_workload.py` check or set them, and leave them at the last shape used.
