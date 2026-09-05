---
name: integration-steps
description: "THE LIVE integration document — step-by-step execution guide with a pass criterion per step and every measured number; the authoritative detail behind gemv-integration-status"
metadata:
  node_type: memory
  type: project
  modified: 2026-09-03T00:00:00.000Z
---

# Integration — Step-by-Step Execution Guide

Companion to [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md). The plan says *what* and *why*;
this says *what to run next*, in order, with a pass criterion for every step.

**Starting point (2026-08-10):** RTL verified in Vivado simulation; U280 + Vitis 2021.1
working on `coroni` (§0.1 ✅). Everything below §0.1 is open.

**Division of labour.** Steps marked **[C]** are mine — I write the file, you review it.
Steps marked **[S]** are yours — you run them on the server and paste me the output. I never
run Vivado, Vitis, `vitis_hls`, `v++` or hardware.

**Gates** are marked ⛔ — do not proceed past one until it passes. Everything is dense-first.

Environment, sourced in every shell:

```bash
source /opt/Xilinx/Vitis/2021.1/settings64.sh
source /opt/xilinx/xrt/setup.sh
export PLATFORM=/opt/xilinx/platforms/xilinx_u280_xdma_201920_3/xilinx_u280_xdma_201920_3.xpfm
export BDF=0000:af:00.1
```

Always pass the **absolute `.xpfm`** to `v++ --platform`. The path above is v++-verified
(2026-08-22).

> **Correction to §0.1:** the poison 2022.2 platform does **not** break `v++`. Its platform
> scan enumerated all 15 installed platforms, including `xilinx_u280_gen3x16_xdma_1_202211_1`,
> without error. The failure is specific to `platforminfo -l`. The `~/platforms_2021` symlink
> workaround is therefore not needed for builds -- just pass the absolute path.

---

## Scope — 8 cores, fixed

**The thesis question is dense vs sparse. 8 cores is the fixed reference configuration used
to answer it, not a variable** (user, 2026-08-18). The core-count sweep may be attempted
some day but is not planned.

Consequences:

- **Stage B (S5–S6) is dropped**, not deferred — the §1.1/§1.2 parameterisation audit exists
  only to enable §7.4.
- **§7.4 (core count) is out of scope.** §7.2 (HBM vs DDR) and §7.3 (bus bandwidth) stay:
  those vary the *memory system* under a fixed architecture pair, which serves the main
  question directly.
- Step numbering is left unchanged so earlier notes and cross-references still resolve;
  S5–S6 are simply marked out of scope below.

If the sweep is ever revisited, note the hidden cost: the AXIS wrapper's port list is flat
and hand-written (VHDL cannot generate ports from a generic), so every core count needs a
**regenerated wrapper and a regenerated RTL Kernel Wizard config**, on top of re-verifying
the RTL.

Rough shape of the whole thing:

| Stage | Steps | What it delivers | Wall time |
|---|---|---|---|
| A | S1–S4 | AXIS splitter wrapper, dense, simulated | 2–3 days |
| ~~B~~ | ~~S5–S6~~ | ~~§1.1 parameterisation audit~~ — **out of scope** | — |
| C | S7–S9 | Wizard packaging smoke test → `krnl_gemv_dense.xo` | 1–2 days |
| D | S10–S14 | HLS data movers (2 tiny kernels), csim + cosim clean | 2–3 days |
| E | S15–S18 | Link config + host + **hw_emu bit-exact** | 3–5 days (the real debugging) |
| F | S19–S22 | **Dense on hardware, bit-exact + measured** | ✅ **DONE** |
| G | S23–S26 | **Sparse on hardware, bit-exact at all 4 sparsities + runtime-reconfigured** | ✅ **DONE 2026-08-26** |
| H | S27–S29 | Sweeps (§7.1 architecture, §7.2 HBM/DDR, §7.3 bandwidth) | ← **CURRENT** |
| I | S31–S33 | Measurements + write-up | ongoing |

---

## Stage A — the AXIS splitter wrapper (the §0.2b blocker)

### S1 [C] — write `dense_gemv_axis.vhd`

Thin wrapper around the untouched `dense_gemv`, exposing **14 flat AXIS interfaces**:

| Interfaces | Direction | Signals per interface |
|---|---|---|
| `s_axis_w0` … `s_axis_w7` | slave | `tdata[255:0]`, `tvalid`, `tready`, `tkeep[31:0]`, `tlast` |
| `s_axis_a0`, `s_axis_a1` | slave | same |
| `m_axis_c0` … `m_axis_c3` | master | same |

Four zero-logic jobs (§0.2b): slice `tdata` little-endian (PC *k* = `tdata[256k+255:256k]`),
slice the `tvalid`/`tready`/`tlast` vectors to scalars, drive every master `tkeep` to all-1s
and ignore every slave `tkeep`, and keep the port list flat (no records, no arrays).

**Deliverable:** `GEMV_Dense_Source/Design/dense_gemv_axis.vhd`.

### S2 [C] — write `dense_gemv_axis_TB.vhd`

The existing `dense_TB` with the DUT swapped for the wrapper and the stimulus fanned out to
per-channel ports. Same `.hex` files, same `golden.txt`, same compare script — the point is
that the wrapper is provably transparent.

### S3 [S] ⛔ — simulate the wrapper (GUI)

In the **GEMV_Dense** Vivado project:

1. **Add the two files.** Flow Navigator → *Add Sources*
   - *Add or create design sources* → `GEMV_Dense_Source/Design/dense_gemv_axis.vhd`
   - *Add or create simulation sources* → `GEMV_Dense_Source/Simulation/dense_gemv_axis_TB.vhd`
2. **Set the simulation top.** Sources → *Simulation Sources* → right-click
   `dense_gemv_axis_TB` → **Set as Top**.
3. **Run.** Flow Navigator → **Run Simulation → Run Behavioral Simulation**, then let it run
   to completion (the TB calls `std.env.stop` itself; don't cap the run time).
4. **Check the Tcl Console** for the two end-of-run reports: the beat count, and
   `TKEEP ok on every output beat`.
5. **Compare** — the TB writes to the same `output.txt` / `tlast.txt` paths as `dense_TB`, so
   the existing script runs unchanged: `python3 GEMV_Dense_Source/Emulation/compare_dense_py36.py`
6. **Run it a second time with stress OFF**: edit the three knobs at the top of
   `dense_gemv_axis_TB.vhd` to `WGT_GAP_MAX = 0`, `ACT_GAP_MAX = 0`, `TREADY_STALL = 0.0`,
   re-run, re-compare.

**Pass:** bit-exact against `golden.txt` on **both** stress settings, and the beat count
matches the pre-wrapper `dense_TB` run. Any difference at all means the wrapper is not
transparent; stop and send me the diff.

> Two traps you have hit before: Vivado silently reuses a stale netlist when an edited
> file's mtime looks old — if a re-run behaves like the old code, right-click the file →
> *Refresh Hierarchy*, or touch it — and a stress-ON-only run can hide drain-latency faults,
> which is why step 6 is not optional.

### S4 [S] ✅ **PASSED 2026-08-17** — OOC synthesis of the wrapper

1. Sources → right-click `dense_gemv_axis` → **Set as Top**
2. **Check the clock port in `timing.xdc`.** It must constrain **`ap_clk`** when
   `dense_gemv_axis` is the top (the file carries both lines; one is commented out).
3. Settings → Synthesis → More Options: `-mode out_of_context` (the top has ~3600 ports; a
   bonded build cannot place)
4. Run synthesis + implementation, then **Reports → Report Utilization** and
   **Report Timing Summary**

**Pass:** **utilisation identical** to the pre-wrapper dense row in `results/Utilization.xlsx` --
LUT / LUTRAM / FF / DSP / BRAM / F7-F8 muxes. That is the check that proves the wrapper is
free; a wrapper that costs LUTs is a wrapper that has logic in it.

**WNS is NOT expected to be bit-identical.** Renaming the top-level ports and adding a
hierarchy level perturbs placement and routing even when the netlist is logically the same,
so a few tens of ps of drift is noise, not a regression. The bar for timing is: positive
WNS, TNS = 0.000, **0 failing endpoints**, and no large move away from the banked value.
Measured 2026-08-16: **+0.108 ns** vs the banked **+0.132 ns** -- 24 ps, ~1% of the 2.222 ns
period. Fine.

> ⚠ **`timing.xdc` does not constrain the wrapper** (found the hard way, 2026-08-16).
> It reads `create_clock … [get_ports clk]`, but the wrapper's clock port is **`ap_clk`**
> — the RTL Kernel Wizard fixes that name on the kernel top, so the rename is required and
> permanent. An empty `get_ports` result makes `create_clock` a no-op **with only a
> warning**, and the run completes looking healthy:
>
> | Symptom | What it actually means |
> |---|---|
> | `WNS: inf`, `WHS: inf` | no timing paths were analysed at all |
> | `All user specified timing constraints are met` | vacuously true — there were none |
> | `Check Timing (258595)` | every endpoint unconstrained |
>
> **`WNS = inf` is never a pass**, and it is the symptom to watch for. `timing.xdc` now
> carries both `create_clock` lines with the unused one commented out; switch them when you
> switch tops. It cannot self-select: **XDC files reject Tcl control flow** -- an `if` gives
> *"[Designutils 20-1307] Command 'if' is not supported in the xdc constraint file"*. The
> same trap awaits `two2N_axis` at S23, whose clock is also `ap_clk`.
>
> Utilisation from an unconstrained run is not comparable either — implementation has no
> timing pressure, so it will not have optimised the same way. Re-run both reports.

**RESULT 2026-08-17** (impl, `dense_gemv_axis` top, `ap_clk` constrained at 2.222 ns):

| Resource | `dense_gemv` banked | `dense_gemv_axis` | delta |
|---|---|---|---|
| **LUT as Logic** | 38,481 | **38,481** | **0** |
| **DSPs** | 512 | **512** | **0** |
| **Block RAM Tile** | 64 | **64** | **0** |
| **CARRY8** | 2,888 | **2,888** | **0** |
| **F7 / F8 Muxes** | 0 / 0 | **0 / 0** | **0** |
| CLB LUTs total | 44,240 | 44,241 | +1 |
| LUT as Memory (SRL) | 5,759 | 5,760 | +1 |
| CLB Registers | 76,210 | 76,202 | -8 |
| CLB (placement) | 10,809 | 11,541 | +732 |
| WNS | +0.132 ns | +0.108 ns | -24 ps |

**LUT as Logic identical to the digit** = the wrapper contributed zero logic, which is the
whole claim. The +1 SRL and -8 FFs move together (one shift-register inference boundary
shifted a stage; a wrapper that added logic could not *reduce* the register count), and CLB
count and WNS are placement metrics that drift run to run.

**Stage A is complete: the verified dense engine is now packageable without having been
touched.**

---

## Stage B — §1.1 static parameterisation audit — ❌ **OUT OF SCOPE (2026-08-18)**

*Kept for reference only. 8 cores is fixed; see Scope above. Do not schedule these.*

### ~~S5 [C] — audit the generics~~ *(out of scope)*

I read both tops and every sub-module and report where the §1.1 relationships
(`W_PCS = CORES_NUM`, `IND_BITS = 80·CORES_NUM`, `C_PCS = CORES_NUM/2`, …) are *derived*
versus *hardcoded*. Output: a table of every place `CORES_NUM ≠ 8` would break.

### ~~S6 [S] — elaborate at 4 and 16 cores~~ *(out of scope)*

```bash
# elaborate only — no synthesis, no simulation. Minutes, not hours.
```

**Pass:** both tops elaborate cleanly at `CORES_NUM = 4` and `CORES_NUM = 16`. If they do
not, the core sweep (§7.4) is dead until fixed, and you know that now rather than in
October. Full functional re-verification is deferred to S23 — see the note above.

---

## Stage C — package the compute kernel

### S7 [S] ✅ **PASSED 2026-08-10** — RTL Kernel Wizard smoke test

This is the cheapest possible test of the biggest unknown in the plan. In Vivado 2021.1,
open a scratch project for `xcu280-fsvh2892-2L-e` and:

```tcl
create_ip -name rtl_kernel_wizard -vendor xilinx.com -library ip \
          -module_name krnl_gemv_dense -dir ./build
report_property [get_ips krnl_gemv_dense]     ;# <-- paste me this output
```

**Pass:** the wizard accepts **14 AXI4-Stream interfaces** with **`ap_ctrl_none`**, zero
`m_axi` and zero scalar arguments.

Since writing this I pulled the reference `gen_xo.tcl` from the **2021.1 branch**, so five
property names are now *confirmed*, not guessed: `KERNEL_NAME`, `KERNEL_CTRL`,
`NUM_INPUT_ARGS`, `NUM_M_AXI`, `NUM_AXIS`, `AXIS00_NAME`… — and `create_ip` takes
`-version 1.0`. That shrinks S7 to the three things the reference cannot answer, because it
only ever uses **two** streams and sets no direction or width:

- **the stream-count ceiling** — does it go to 14? to 17? (the reference proves only 2)
- **whether `AXISnn_MODE` / `AXISnn_WIDTH` exist**, and how direction is decided when only
  `AXISnn_NAME` is set
- **the generated port list** — above all, whether each stream carries `TKEEP`

**Send me `s7_out/s7_report.txt`.**

**RESULT (2021.1 GUI, RTL Kernel Wizard 1.0, `xcu280-fsvh2892-2L-e`):** all four questions
pass. `ap_ctrl_none` selectable with nothing greyed; scalars → 0; **Global Memory forced to
0 and locked** once free-running is chosen; **streams min 1, max 32**, each with Name /
Mode (Master|Slave) / Width in **bytes** from {1,2,4,8,16,32,64}.

Three defaults that must be overridden, all caught from the dialog rather than the log:

- **`Has reset` defaults to `0`** → set to **1** (our blocks need `resetn`)
- **Width defaults to 64 B (512 b)** → set to **32 B (256 b)** on every stream
- names default to `axis00…` with **alternating Master/Slave** → override all three columns

Bonus finding: 64 B is on the width menu, so the §7.3 `PC_WIDTH` 256→512 experiment needs no
fallback. And 32 streams is a hard ceiling — 16-core sparse (32 PCs) sits exactly on it.

The §3.3 hand-written `kernel.xml` fallback is **not needed** and stays documented only as
insurance.

### S8 [S then C] — capture the wizard Tcl, then write `gen_xo_dense.tcl`

**S8a ✅ DONE 2026-08-10.** Captured from the GUI; property names recorded in §3.2. Three
differed from the reconstruction: `NUM_RESETS` (not `HAS_RESET`), `AXISnn_NUM_BYTES` (not
`AXISnn_WIDTH`), and mode values `read_only`/`write_only` (not `Master`/`Slave`). Swap target
identified as `krnl_gemv_dense_example`. **S8b is unblocked** and waits only on S3/S4.

*(original instructions kept below for the sparse repeat at S23)*

**S8a [S] — let the GUI author the script.** Configure the wizard fully for dense, click
**OK**, and copy what the Vivado **Tcl Console** echoes: every GUI action prints its Tcl
equivalent, so the `set_property -dict {…}` it emits *is* the authoritative property list,
with no transcription risk. Settings:

| Field | Value |
|---|---|
| Kernel name | `krnl_gemv_dense` |
| Kernel type | `RTL` |
| Kernel control interface | **`ap ctrl none`** |
| Number of clocks | `1` |
| **Has reset** | **`1`** (default is 0) |
| Scalar arguments | `0` |
| AXI master interfaces | `0` (locked) |
| **AXI4-Stream interfaces** | **`14`** |

Then per stream — Name / Mode / **Width `32` bytes** on every row:

| # | Name | Mode | | # | Name | Mode |
|---|---|---|---|---|---|---|
| 00 | `s_axis_w0` | Slave | | 08 | `s_axis_a0` | Slave |
| 01 | `s_axis_w1` | Slave | | 09 | `s_axis_a1` | Slave |
| 02 | `s_axis_w2` | Slave | | 10 | `m_axis_c0` | Master |
| 03 | `s_axis_w3` | Slave | | 11 | `m_axis_c1` | Master |
| 04 | `s_axis_w4` | Slave | | 12 | `m_axis_c2` | Master |
| 05 | `s_axis_w5` | Slave | | 13 | `m_axis_c3` | Master |
| 06 | `s_axis_w6` | Slave | | | | |
| 07 | `s_axis_w7` | Slave | | | | |

Names carry through to the kernel's interface names, so these are exactly the strings the
`stream_connect` lines in S15 will use — and they match the wrapper port names from S1, so
kernel and wrapper wire up name-for-name.

**S8b [C]** — from that echoed Tcl I write `gen_xo_dense.tcl`: the same properties (all 14
`AXISnn_MODE` set explicitly), plus `generate_target`, `open_example_project`, the top-file
swap below, and `package_xo`.

**The swap, precisely.** The generated `krnl_gemv_dense.v` says *"Do not modify module name,
parameters or ports"* at its header and *"Remove to insert custom logic"* at its body — so we
keep the header verbatim (that is what `package_xo` reads the 14 AXIS interfaces from) and
replace only the `krnl_gemv_dense_example` instance with `dense_gemv_axis`. The edited top is
checked in at `GEMV_Dense_Source/Design/krnl_gemv_dense.v`; it overwrites the generated one
in the `_ex` project. The five placeholder files (`_example`, `_example_vadd_axis`,
`_example_adder`, `_example_number_generator`, `_example_counter`) then become dead and are
removed from the project.

Files that go into the `_ex` project alongside it: `dense_gemv_axis.vhd`,
`top_module_dense.vhd`, and every module it pulls in (`c_core_dense`, `c_block_dense`,
`weights_fifo_dense`, `vector_fifo_4.0`, `vector_fifo_cycle_4.0`, `c_fifo_4.0`, the three
`*_wrapper_4.0` files) plus the IP the design uses.

### S9 [S] ✅ **PASSED 2026-08-18** — build `krnl_gemv_dense.xo`

`scp gen_xo_dense.tcl` to the server, then in the **Vivado Tcl Console with no project
open**:

```tcl
source /home/skoulas/gen_xo_dense.tcl
```

It runs six phases: wizard IP -> example project -> swap in `krnl_gemv_dense.v` -> add the
11 design files and regenerate the 7 IP cores -> `ipx::` re-package -> `package_xo`.
GUI-safe (never calls `exit`). To retry part of it after a hand fix:

```tcl
set ::GEN_XO_PHASES {5 6}
source /home/skoulas/gen_xo_dense.tcl
```

**Pass, all three:**

1. `SUCCESS: /home/skoulas/GEMV_Dense/krnl_gemv_dense.xo` printed;
2. ~~the file is megabytes, not kilobytes~~ — **THIS CRITERION IS WRONG.** A correct `.xo`
   here is ~120–135 KB (dense: 117,517 bytes, ran bit-exact on hardware; sparse: 132,234).
   **Verify by listing the archive** (`unzip -l <file>.xo`) and confirming the VHDL and the
   7 `.xci` are inside. An empty wizard shell is what links happily and computes nothing,
   and it is not distinguishable by size;
3. no interface-inference or unassociated-port warnings in the log (§3.5) -- these are
   quiet at package time and only bite at link time.

Paste me the log either way.

> **No `sw_emu` for this design.** RTL kernels need a hand-written C model for `sw_emu`
> (the AMD example ships `myadder_cmodel.cpp`). Writing one for a 2:M GEMV engine is not
> worth it — the Python golden model already fills that role. Go straight to `hw_emu`.

---

## Stage D — the HLS data movers

**RESULT 2026-08-18:** `krnl_gemv_dense.xo` built (117,517 bytes). Verified by listing the
archive rather than by size -- it contains `component.xml` (136 KB, all 14 interfaces),
`kernel.xml`, and the full design: `dense_gemv_axis.vhd`, `krnl_gemv_dense.v`,
`top_module_dense.vhd`, `c_core_dense.vhd`, `c_block_dense.vhd`, `weights_fifo_dense.vhd`,
the FIFO and wrapper files, and all 7 IP `.xci`. **Stage C complete.**

Two notes from the run:

- **`[Vivado 12-4404]` "no C-model files" is expected** -- it only means `sw_emu` is
  unavailable, which we chose deliberately (see the box below).
- The archive also carries the wizard testbench's **AXI-Stream Verification IP**
  (`s_axis_w0_vip` etc.), pulled in by `ipx::package_project -import_files`. This is what
  the wizard's own flow does, and they belong to the simulation file group -- but **if
  `v++` complains about VIP at S17, strip `krnl_gemv_dense_tb.sv` and its VIP from the
  example project and re-run phases 5-6.**

**What went wrong on the way, so it is not repeated for sparse at S23:** my first phase 5
tried to `ipx::open_core` a core that did not exist yet. The wizard's example project ships
`imports/package_kernel.tcl`, whose `package_project` proc calls `ipx::package_project
-import_files` -- that is what *creates* `component.xml`, pulls in every file added to the
project, re-applies all 14 interface definitions via `edit_core`, and runs
`check_integrity -kernel/-xrt`. Use the wizard's script; do not hand-roll the `ipx::`
sequence.

### S10 [C] — hex → binary memory images

A small Python script converting `weights.hex` / `activations.hex` (and `indices.hex` for
sparse) into per-PC `.bin` images, plus the reverse for checking output. I can run this
locally — it is the one thing in this project I execute myself.

### S10b [S] — generate the **V=1024** stimulus

**No code needed** -- `gemv_dense_gen.py` is already parameterised, and this exact case is
item 6 of its own verification ladder.

| Case | Command | V | rows | weight beats |
|---|---|---|---|---|
| current (verified) | `--nwin 2 --nlaps 32` | 64 | 2048 | 1024 |
| **§7.1 reference** | `--nwin 32 --nlaps 16` | **1024** | **1024** | **8192** |

```bash
cd ~/GEMV_Dense/GEMV_Dense_Source/Emulation
python3 gemv_dense_gen.py --nwin 32 --nlaps 16
```

Needs PyTorch (the generator only; the compare scripts are standard-library). **Keep both
stimulus sets** -- the small one is what hw_emu should use, since the large one would
simulate for hours. Regenerate the small case with `--nwin 2 --nlaps 32` when switching back.

Not required for Stages C-E; required before Phase 6 step 2 and all of Stage I.

### S11 [C] — write `krnl_mm2s.cpp` and `krnl_s2mm.cpp`

Per §2.2, as decided in §0.2e: **two tiny kernels, one PC each**, replicated at link time.
`krnl_mm2s` = 1 `m_axi` + 1 AXIS + `n_beats`; `krnl_s2mm` = 1 AXIS + 1 `m_axi` + `n_beats`.
`ap_axiu<256,0,0,0>`, one `PIPELINE II=1` loop, `keep = -1`, TLAST from the loop bound, and
the write side is count-based and never inspects TLAST — all matching the reference. **No
`DATAFLOW` anywhere.** Plus a C testbench that writes the stream to a file.

### S12 [S] ✅ **PASSED 2026-08-22** — `csim`

```bash
vitis_hls -f run_hls_mm2s.tcl      # csim only
diff mm2s_pc0.out GEMV_Dense_Source/Emulation/weights_pc0.hex
```

One kernel, one PC — so this checks *all* 14 CUs at once: they are the same kernel with
different arguments and different buffers.

**Pass:** every emitted stream file is **beat-identical to the existing `.hex` stimulus**.
This is the highest-value shortcut in the whole plan (§2.3) — if the movers reproduce the
files the RTL was verified against, the entire compute-side verification chain carries over
untouched.

### S13 [S] ✅ **II=1 CONFIRMED 2026-08-22** — `csynth` + `cosim`

**Pass:** cosim clean; note the reported II and the achieved burst length. If II > 1 on the
`krnl_mm2s` loop, every one of the 10–13 read CUs bottlenecks the engine — send me the
schedule report and I will look at the pragmas.

**RESULTS 2026-08-22 (dense stimulus: 1024 weight beats, 2 activation beats):**

- **csim:** `PASS: 1024 beats reproduce weights.hex exactly, TLAST/TKEEP correct` and
  `PASS: 2 beats reproduce activations.hex exactly`. The movers reproduce the files the RTL
  was verified against -- the existing verification chain carries to hardware unchanged.
- **csynth:** loop `mm2s` **II achieved = 1** (target 1, Pipelined yes). Iteration latency 2.
  Trip count reports `?` because `n_beats` is a runtime argument -- expected.
- **Timing:** target 3.33 ns, estimated **2.433 ns** -> the movers would close near 400 MHz.
  They will not be the limiter when the kernel clock is pushed at S22.
- **cosim:** blocked by a Vitis 2021.1 toolchain bug, not our code -- the shipped
  `mpfr.h` expects `__gmp_const`, removed from GMP years ago; cosim's testbench rebuild leaks
  Ubuntu's `gmp.h`. `ap_uint<256>` (>64 bits) is what pulls GMP in at all. Workaround in
  `run_hls_movers.tcl`: `-cflags "-D__gmp_const=const"` on the TB files, plus a `DO_COSIM`
  toggle. **Skippable** -- csynth gives the II, and hw_emu (S18) exercises the real mover RTL
  end-to-end through the actual AXI stack, which is strictly stronger than cosim.

### S14 [S] — compile to `.xo`

```bash
v++ -c -t hw_emu --platform $PLATFORM -k krnl_mm2s -o krnl_mm2s.hw_emu.xo krnl_mm2s.cpp
v++ -c -t hw_emu --platform $PLATFORM -k krnl_s2mm -o krnl_s2mm.hw_emu.xo krnl_s2mm.cpp
```

**Two `.xo` files, whatever the PC count** — replication is a link-time `nk=` matter.

**Note:** HLS `.xo` files are **target-specific** — you need a separate `-t hw` pair later
(S19). The wizard-packaged RTL `.xo` is not; the same file links for both targets.

---

## Stage E — link and hardware emulation (where the real debugging happens)

### S15 [C] ✅ **DONE** — `dense_hbm.cfg`

```ini
[connectivity]
nk=krnl_mm2s:10:mm2s_w0.mm2s_w1.mm2s_w2.mm2s_w3.mm2s_w4.mm2s_w5.mm2s_w6.mm2s_w7.mm2s_a0.mm2s_a1
nk=krnl_gemv_dense:1:gemv
nk=krnl_s2mm:4:s2mm_c0.s2mm_c1.s2mm_c2.s2mm_c3

# 10 read PCs + 4 write PCs = 14
sp=mm2s_w0.in:HBM[0]
# ... w1..w7 -> HBM[1..7], a0,a1 -> HBM[8,9]
sp=s2mm_c0.out:HBM[10]
# ... c1..c3 -> HBM[11..13]

stream_connect=mm2s_w0.out:gemv.s_axis_w0
# ... 10 input streams
stream_connect=gemv.m_axis_c0:s2mm_c0.in
# ... 4 output streams
```

The explicit `nk=` CU names are not cosmetic: without them every `sp=` and
`stream_connect=` refers to `krnl_mm2s_1`…`krnl_mm2s_10` by position, and one transposed
digit silently binds a weight PC to an activation stream.

### S16 [C] ✅ **DONE** — `host.cpp` (OpenCL, self-contained)

Per §4, as decided in §0.2e. I vendor `xcl2.cpp`/`xcl2.hpp` from the examples repo
(Apache-2.0) and write the host to drive **14 CUs** — 10 `krnl_mm2s` + 4 `krnl_s2mm` — with
the compute kernel never referenced. Four things I will get right, each of which is a
silent-wrong-answer or deadlock bug otherwise:

- **CU-specific kernel handles**: `cl::Kernel(program, "krnl_mm2s:{mm2s_w0}")`. Bare
  `"krnl_mm2s"` lets the runtime pick any of the 10, each bound to a different HBM PC —
  wrong data, no error.
- **out-of-order queue** (`CL_QUEUE_OUT_OF_ORDER_EXEC_MODE_ENABLE`). On a default in-order
  queue the CUs serialise and a producer waits on a consumer that cannot start: deadlock.
- **never `setArg` the stream argument** — k2k stream ports take no argument.
- **device selection is an argv**: `hw_emu` has no BDF, hardware has three cards. Index `0`
  in emulation, `$BDF` on hardware, and assert the name is a U280 before proceeding.

Plus a `--tiny` mode (1 window, 1 lap) for emulation.

### S17 [S] ✅ **DONE 2026-08-22** — build for hw_emu

```bash
emconfigutil --platform $PLATFORM --nd 1        # generates emconfig.json — do not skip
export XCL_EMULATION_MODE=hw_emu

v++ -t hw_emu --platform $PLATFORM --config dense_hbm.cfg --kernel_frequency 300 \
    -l -o gemv_dense.hw_emu.xclbin \
    krnl_mm2s.hw_emu.xo krnl_gemv_dense.xo krnl_s2mm.hw_emu.xo

# OpenCL host (xcl2), per 0.2e -- flags taken from the reference Makefile
g++ -Wall -O0 -g -std=c++1y -I./xcl2 -I$XILINX_XRT/include \
    xcl2/xcl2.cpp host.cpp \
    -L$XILINX_XRT/lib -lOpenCL -lrt -lstdc++ -pthread -o host
```

**Pass:** link completes. Read the log for stream-connection warnings — a mistyped port name
in `stream_connect` is a *warning*, not an error, and it leaves the stream dangling.

### S18 [S] ✅✅ **PASSED 2026-08-22** — run hw_emu, tiny case

```bash
export XCL_EMULATION_MODE=hw_emu
./host 0 gemv_dense.hw_emu.xclbin --tiny
```

**Pass:** one output beat, **bit-exact against `golden.txt`**. This is the single most
important gate in the plan — it is what catches AXI protocol errors, bank misassignment,
argument order, and TLAST landing on the wrong beat through the real interconnect.

Expect to spend real time here. If it **hangs**, the likely causes in order:
1. a `stream_connect` port-name typo leaving a stream dangling — the barrier join waits
   forever for a PC that never arrives. **Check this first**; it is a link *warning*, not an
   error, so the build looks clean;
2. a CU never launched, or `q.finish()` reached while one of the 14 was never enqueued —
   with replicated movers this is an easy off-by-one in the launch loop;
3. skew between PCs exceeding the ingress FIFOs — try the optional `stream_connect` FIFO
   depth (§3.4) before touching anything else.

**Do not "fix" a hang by editing `dense_gemv`.** The compute RTL is verified; per the plan's
corollary, if an experiment seems to require changing it, the cause is almost certainly in
the engine or the link config.

**STAGE E COMPLETE 2026-08-22.**

- Link: 14/14 `stream_connect` resolved, 14/14 `sp=` bindings, 15 CUs, no warnings.
- hw_emu run: 14 CUs launched and completed, 64 rows written.
- `compare_dense_py36.py`: **64 rows, 0 mismatches, DATA: PASS (bit-exact)**.

The whole memory path is proven: host -> HBM -> 10 mover CUs -> AXIS -> engine -> AXIS ->
4 mover CUs -> HBM -> host, through the real interconnect, validated by the unchanged
golden model.

**Gotchas found and fixed in this stage, all recorded above:**
- `[clock]` in the link config is rejected by this platform (2019.2-era, no fixed reference
  clocks) -- use `--kernel_frequency` instead.
- The platform's default `KERNEL_CLK` is **500 MHz**. Harmless in emulation; on hardware it
  becomes the timing target and the engine cannot meet it. **`--kernel_frequency 300` is
  mandatory at S19**, not optional tuning.
- XRT warns `unaligned host pointer` once per buffer -- an extra memcpy per transfer.
  Irrelevant to correctness and to emulation, but it lands inside any wall-clock number, so
  fix it with `posix_memalign(4096, ...)` before reporting throughput at S22.

---

## Stage F — dense on hardware

### S19 [S] ✅ **DONE 2026-08-23** — rebuild the mover `.xo` for `-t hw`, then link

**RESULT:** `gemv_dense.xclbin` 45 MB, no errors, no critical warnings.
**Post-route timing MET at 300 MHz:** WNS **+0.022 ns**, TNS 0.000, **0 failing endpoints**
(821,238 total); hold and pulse-width clean.

Two things learned the hard way:

- **The first attempt died mid-route overnight.** Not OOM (the only logged kill was 2 days
  earlier and hit `vitis_hls`) and not disk (the build used 2.9 GB against 62 GB free) --
  almost certainly the X2Go session being torn down, since `nohup` only shields against
  SIGHUP. **Run long builds inside `tmux`**, which is immune to every kind of disconnect.
  Detach with Ctrl-B then D; `tail -f link_hw.log` to check progress without attaching.
- **22 ps of margin is very tight.** The engine alone made +0.108 ns at *450* MHz
  out-of-context; in-system at *300* MHz it barely closes. The cost is the system around it
  -- 15 CUs, the interconnect to 14 HBM channels, and router-reported CLB congestion.
  **Before drawing any Fmax conclusion at S22, find out which clock domain owns the critical
  path** -- it may well be the memory system, not the compute datapath.

```bash
v++ -c -t hw --platform $PLATFORM -k krnl_mm2s -o krnl_mm2s.hw.xo krnl_mm2s.cpp
v++ -c -t hw --platform $PLATFORM -k krnl_s2mm -o krnl_s2mm.hw.xo krnl_s2mm.cpp

v++ -t hw --platform $PLATFORM --config dense_hbm.cfg --kernel_frequency 300 \
    -l -o gemv_dense.xclbin \
    krnl_mm2s.hw.xo krnl_gemv_dense.xo krnl_s2mm.hw.xo
```

**Hours per build. Batch them overnight**, and check for card contention first — the machine
is shared.

### S20 [S] ✅ **DONE 2026-08-23** — check the card

```bash
xbutil examine --device $BDF --report all
xbutil validate --device $BDF
```

### S21 [S] ✅ **PASSED 2026-08-23** — first hardware run

```bash
unset XCL_EMULATION_MODE
./host $BDF gemv_dense.xclbin --tiny
```

**Pass:** bit-exact. Then walk the ladder without skipping: tiny → full 1024-element vector,
1 lap → full 1024×1024, 16 laps.

**FIRST HARDWARE RUN PASSED 2026-08-23.** Tiny case (V=64, 1 lap, 64 rows) on the physical
U280 at BDF `0000:af:00.1`: 14 CUs launched and completed, `compare_dense_py36.py` reports
**64 rows, 0 mismatches, DATA: PASS (bit-exact)**.

The verification chain is unbroken end to end -- the same Python golden model validates
behavioural sim, post-implementation sim, hw_emu, and hardware.

**Card-sharing note:** `xbutil examine` showed someone else's **Vitis AI DPU** xclbin loaded
before the run (`DPUCAHX8H_*:dpu_0/1/2`, all IDLE). Loading ours reprogrammed over it. With
13 users on that machine, **check the Compute Units list is idle or empty before every
hardware run** -- a `load_xclbin` while another user's design is mid-flight pulls it out from
under them. Card health at the time: Device Ready yes, firewall GOOD, FPGA 36 C, ~30 W idle.

**No rebuild is needed to change problem size.** The host derives every count from the image
files, so the ladder below is regenerate + pack + run, with the same `.xclbin`.

**LADDER COMPLETE 2026-08-23** (each run preceded by `xbutil reset`):

| Rung | nwin | nlaps | V | rows | Result |
|---|---|---|---|---|---|
| 1 | 2 | 1 | 64 | 64 | ✅ bit-exact |
| 2 | 2 | 32 | 64 | 2048 | ✅ bit-exact -- replay buffer over 32 laps |
| 3 | 32 | 1 | 1024 | 64 | ✅ bit-exact -- 32-window load path |
| 4 | 32 | 16 | 1024 | **1024** | ✅ bit-exact -- **both together, the §7.1 configuration** |

Dense is correct on hardware across every geometry tested. Rung 4 is the first to exercise
the multi-window load path and the replay buffer in the same run.

### ✅ S21b — ~~THE ENGINE IS SINGLE-SHOT PER PROGRAMMING~~ **FIXED AND CONFIRMED ON HARDWARE 2026-08-24**

**Symptom.** The first hardware run after an xclbin load is bit-exact. Every run after that
is wrong, with two different signatures:

| Second run uses | Error |
|---|---|
| same geometry, different data | few percent (2.4%, 7.1%) -- right magnitude, wrong values |
| different `nwin` | huge; output ~constant ~780 regardless of problem size |
| same geometry, same data | **passes** -- the stale vector happens to be the right one |

**Proof.** Byte-identical seed-222 stimulus, run twice. Without reset: FAIL (64/64, 7.09%).
After `xbutil reset`: PASS bit-exact. The reset was the only variable.

**Root cause.** `krnl_gemv_dense` is `ap_ctrl_none`, so the host cannot reset it -- `ap_rst_n`
asserts only on FPGA programming. And the engine does not self-restore: `cycle_en_int` is set
to '1' at the first lap boundary (`top_module_dense.vhd:448`) and cleared **only by resetn**,
while the activation-load branch (`:414`) is guarded by `cycle_en_int = '0'`. So run 2 never
loads a new vector -- it recirculates run 1's.

**Why no testbench caught it:** simulation always starts from reset and runs exactly once.
Both the sequential and the concurrent TB pass on stimulus that fails on hardware.

**RESOLVED ON HARDWARE 2026-08-24.** Rebuilt xclbin (46 MB, 2h14m link, WNS **+0.022 ns**,
0 failing endpoints -- identical slack to the pre-fix build, so the teardown cost nothing
measurable). Test: `xbutil reset`, then seed 111 -> **PASS bit-exact**, then seed 222 with
**different data and NO reset** -> **PASS bit-exact**. The workaround below is no longer
required; repeat runs are cheap again, which is what makes the S22 measurement campaign
practical.

**~~WORKAROUND, mandatory for every hardware run until fixed:~~** (historical)

```bash
xbutil reset --device 0000:af:00.1     # answer Y; do NOT paste it inside a command block
./host gemv_dense.xclbin <emu_dir>
```

Consequences: a reset costs a full reconfiguration (seconds), it resets the card for **all**
users, and repeat runs for timing statistics each need their own reset -- so the "run it 3x
and take the best" advice in `host.cpp` only holds with a reset between runs.

**The same bug is in `two2N`** -- identical control structure. It will bite at S24 unless
fixed first.

**FIX IMPLEMENTED AND VERIFIED IN SIMULATION 2026-08-24.** Two causes, not one -- and they
hid each other:

1. **`cycle_en_int` was never cleared at end-of-calculation.** The replay branch does carry
   `if tlast_int_w = '1' then cycle_en_int <= '0';` -- but it sits under
   `if settle='1' then <bubble> elsif cycle_en_int='0' then <load> else <replay>`, and the
   final lap's terminal sets `settle <= '1'` the cycle BEFORE `tlast_int_w` goes high. The
   bubble branch runs instead, so the clear is skipped exactly when it is needed. The engine
   stayed in replay mode forever.
2. **The activation replay FIFO was never emptied**, so replay mode found the previous
   vector and produced plausible-looking wrong numbers.

Either alone would have been obvious: stuck-in-replay with an empty FIFO deadlocks (no
output at all); a stale FIFO with a working mode reset is harmless. Together they produced
new weights x old activations -- which is why it took a hardware run with *different data at
the same geometry* to expose it.

**Fix:** an end-of-calc teardown in `top_module_dense.vhd` that waits for
`flush_drained = flush_total` (so TLAST tagging is untouched), then clears **`cycle_en_int`**,
the flush accounting, `last_win` and `settle`, and pulses a new `flush` input on
`vector_cycle_512` for 8 cycles -- that port ORs into the FIFO's `srst` and gates its enables.
The teardown lives OUTSIDE the settle/load/replay chain, so it always executes. The load
branch is blocked while flushing so an early-arriving next run cannot lose a beat.

**Verified 2026-08-24 with `dense_gemv_axis_rerun_TB`** (new): two calculations, different
seeds, one continuous simulation, no reset. Result: 4 beats, **TLAST seen 2 times**, and
**256 rows bit-exact**. Single-calculation regression also bit-exact (128 rows).

**STILL TO DO:** the other stress setting on the single-run regression; apply the same fix to
`two2N` (identical structure, same bug); rebuild the `.xo` and `.xclbin`. Until the rebuild
lands, the `xbutil reset` workaround below remains in force on hardware.

**Superseded decision (2026-08-23): take the workaround, defer the fix.** Reset before every hardware
run; do not touch the RTL now. Rationale: dense correctness on hardware is already
established, what remains in Stage F is measurement, and a reset per measured run is
tolerable. Editing verified RTL on one afternoon's diagnosis -- with the sparse path still
untouched -- is the worse trade. One calculation per programming is also a defensible
operating model for a free-running accelerator, so it can be *documented* in the thesis
rather than hidden.

**DEFERRED FIX (Option B), if time allows -- revisit at S24:**

1. Clear `cycle_en_int`, `counter_lock` and `last_win` when the end-of-calculation weight
   TLAST is consumed, so the engine returns to its initial state.
2. **Also drain or reset the activation replay FIFO** (`vector_cycle_512` /
   `fifo_gen_vector_cycle`). Without this the next load *appends* to the stale vector and
   you get a differently-corrupted result instead of a fixed one. This is the part that
   makes it real RTL work rather than a one-line change.
3. Re-verify: S3 simulation, both stress settings, plus a NEW test the current TBs cannot
   express -- **two consecutive calculations in one simulation**, which is exactly the case
   no testbench has ever exercised.
4. Rebuild (`.xo` + multi-hour link), then repeat all of it for `two2N`.

Reconsider it at S24: if sparse bring-up needs many hardware iterations, the fix pays for
itself there. If sparse comes up as smoothly as dense did, the workaround is enough.

### S22 [S] ✅ **DONE 2026-08-24** — dense baseline measured

**The measurement problem and how it was solved.** A single run is dominated by XRT launch
overhead -- two identical runs measured 630 us and 348 us on 1.7 us of actual work. Fixed by
(a) problems large enough that compute dominates, and (b) differencing sizes so the fixed
overhead cancels. Tooling: `Emulation/gen_timing_stimulus.py` (writes per-PC `.bin` directly,
tiled valid-bf16 pattern, no golden -- legitimate because the dense datapath is positional and
the FP operators have fixed latency, so values cannot affect timing) and `Vitis/measure.py`
(sweeps sizes, best-of-N per size, emits CSV).

**Raw sweep, V=1024, 5 reps, best-of** -- note the spread collapsing as overhead amortises:

| laps | weight beats | best us | spread% | Mrow/s | GB/s | beats/cyc |
|---|---|---|---|---|---|---|
| 512 | 262,144 | 1263.0 | 16.7% | 25.94 | 53.19 | 0.692 |
| 1024 | 524,288 | 2222.7 | 7.2% | 29.48 | 60.44 | 0.786 |
| 2048 | 1,048,576 | 4069.2 | 3.1% | 32.21 | 66.03 | 0.859 |
| 4096 | 2,097,152 | 7502.8 | **2.8%** | 34.94 | 71.63 | 0.932 |

**Differential (overhead cancels):**

| pair | delta beats | delta us | Mrow/s | GB/s | beats/cyc |
|---|---|---|---|---|---|
| 512->1024 | 262,144 | 959.7 | 34.14 | 69.99 | 0.910 |
| 1024->2048 | 524,288 | 1846.4 | 35.49 | 72.76 | 0.946 |
| 2048->4096 | 1,048,576 | 3433.6 | 38.17 | 78.25 | **1.018** |

**HEADLINE: dense runs at essentially ONE WEIGHT BEAT PER CLOCK -- its architectural
roofline.** 1.018 is not a real overshoot (impossible); it is the best-of estimator being
slightly optimistic, 1.8% over against 2.8% measured spread. **Quote it as 1.0**, or
">=0.95, consistent with unity within measurement error".

> ⚠️ **WHICH DENSE NUMBER TO QUOTE — read this before using the figures above.**
> TWO dense measurement runs exist and they are NOT the same data. The tables above are
> from the first run (5 reps, best_us 1263.0 / 2222.7 / 4069.2 / 7502.8). The file
> `results/dense_300MHz.csv` is a LATER, independent run (best_us 1350.0 / 2248.3 / 3962.5 /
> 7559.1) and it is the one the §7.1 comparison and `results/GEMV_Hardware_Results.xlsx` use.
>
> **The authoritative dense figures are the least-squares FIT in `results/dense_300MHz.csv`:**
>
> | | value |
> |---|---|
> | throughput | **36.99 Mrow/s** |
> | bandwidth | **75.83 GB/s** |
> | beats/cycle | **0.9863** |
> | fixed overhead | 457.9 us |
> | R^2 | 0.99991 |
>
> Use the FIT, not the 38.17 differential above: the fit uses all four sizes so one lucky
> sample moves it far less, and — decisively — every sparse figure is a fit, so comparing a
> dense differential against a sparse fit would be comparing two different estimators. The
> two agree on the physics either way (~1 beat/cycle); they differ by ~3%, which is the
> estimator difference, not a change in the design.

Everything cross-checks at 300 MHz:

| | theory | measured (marginal) |
|---|---|---|
| beats/cycle | 1.0 | 1.018 |
| throughput | 37.5 Mrow/s | 38.17 |
| MAC rate | 38.4 GMAC/s (64 blocks x 2 MACs x 300 MHz) | ~38 |
| weight bandwidth | 76.8 GB/s (8 PCs x 32 B x 300 MHz) | 78.25 |

**The design is KERNEL-CLOCK-BOUND, not memory-bound.** ~77 GB/s achieved where the 10 input
channels could carry 96 GB/s at 300 MHz, and where HBM itself offers 460 GB/s across 32
channels. The limit is the 300 MHz ceiling, which comes from in-system timing (+0.022 ns).

**Consequence for the sparse comparison:** if sparse also reaches ~1 beat/cycle, the speedup
is *exactly* the ratio of weight beats moved -- the architectural claim, measured rather than
assumed. Any shortfall below 1.0 is the measured cost of the gather and the index streams.

A least-squares fit over all sizes was added to `measure.py` as a third block (raw and
differential kept): it reports the **fixed launch overhead** as the intercept, the marginal
rate as the slope, and R^2 so a bad fit is visible.

### ~~S22 [S] — record dense baseline~~ (original instructions)

`report_utilization`, `report_timing_summary` (post-route, from the `_x` build directory),
kernel wall-clock from XRT, and `xbutil examine` power. **This is where the clock decision
gets made** — if 300 MHz closes easily, rebuild higher and find where dense stops closing.
Dense meeting a higher clock than sparse is itself a thesis result.

---

## Stage G — sparse

### S23 [C+S] — repeat S1–S21 for `two2N`

Everything is the same shape with three deltas:

1. **17 streams, not 14** — `two2N_axis.vhd` adds `s_axis_ind0..2`. Their TLAST is
   **declared and left unconnected**: `two2N` has no index `tlast` port (weight TLAST is
   the end-of-calculation marker, activation TLAST the end-of-vector one), but the AXIS
   rules require the signal to exist and `krnl_mm2s` drives it. Nothing is fabricated —
   the marker simply has no meaning on that channel. `two2N_axis_TB` drives it anyway, so
   the discard is *proven* harmless rather than assumed.
2. **Three more `krnl_mm2s` CUs** (`nk=krnl_mm2s:13:...`) for the index PCs — no new kernel
   source. The **2-bit sparsity code sits in index PC2 padding, bits [641:640]**, written
   into the buffer by the Python packer; no mover knows about it.
3. `sp=` runs to 17 PCs; `stream_connect` to 17 lines.

**Sub-steps** (owner: [C] = Claude, [S] = you on the server):

| # | Owner | What | State |
|---|---|---|---|
| 1 | [C] | `top_module_2N.vhd` — end-of-calculation teardown (the S21b fix) | ✅ done |
| 2 | [C] | `vector_fifo_cycle_4.0.vhd` — `flush` port; sparse copy synced from dense | ✅ done |
| 3 | [C] | `two2N_axis.vhd` — 17-interface AXIS splitter wrapper | ✅ done |
| 4 | [C] | de-torch `gemv4_cosim_gen.py` (no PyTorch on the server) | ✅ done — **7/7 cases bit-identical** |
| 5 | [C] | `two2N_axis_TB.vhd` — wrapper stress TB (+ index-TLAST and TKEEP checks) | ✅ done |
| 6 | [C] | `two2N_axis_rerun_TB.vhd` — two calculations, no reset, **different sparsities** | ✅ done |
| 7a | [S] | copy files, regenerate stimulus, run the wrapper TB **stress ON** | ✅ **PASS 2026-08-25** |
| 7b | [S] | same TB, **stress OFF** (all three knobs 0/0/0.0) | ⚠️ **UNCONFIRMED** — a PASS was reported but never identified as the stress-OFF run |
| 8 | [S] | run the rerun TB → proves the sparse single-shot fix | ✅ **PASS 2026-08-25** |
| 9 | [S] | OOC impl, wrapper vs bare `two2N` on the same RTL | ✅ **PASS 2026-08-25 — wrapper is transparent** |
| 10 | [S] | RTL Kernel Wizard, 17 streams, capture the Tcl | ✅ done 2026-08-25 |
| 11 | [C] | `krnl_gemv_sparse.v` + `gen_xo_sparse.tcl` | ✅ done 2026-08-25 |
| 12 | [S] | build `krnl_gemv_sparse.xo` | ✅ **built 2026-08-25** (132,234 B) |
| 13 | [C] | `hex_to_bin.py` + `host_sparse.cpp` for 13 movers | ✅ done 2026-08-25 |
| 14 | [C] | `sparse_hbm.cfg` — 17 `sp=` + 17 `stream_connect=` | ✅ done 2026-08-25 |
| 15a | [S] | link + run `hw_emu`, bit-exact | ✅ **PASS 2026-08-25** |
| 15b | [S] | link for `-t hw` @ 300 MHz | ❌ **TIMING FAILED 2026-08-26** — WNS −0.480 ns |
| 15c | [S] | rebuild @ 250 MHz | ❌ **STILL FAILS 2026-08-26** — WNS −0.177 ns, 687 endpoints |
| 15d | [S] | rebuild @ 225 MHz | ✅ **CLOSES 2026-08-26** — kernel WNS +0.048 ns, 0 failing |
| 15e | [S] | first hardware run | ✅ **BIT-EXACT 2026-08-26** — 64 rows, 0 mismatches |
| 16 | [C] | `Vitis/run_ladder.sh` — 4 sparsities + reconfig, one command | ✅ done 2026-08-26 |
| 17 | [S] | run the ladder on hardware | ✅ **ALL 5 PASS 2026-08-26** (see S25/S26) |
| 18 | [C] | sparse `gen_timing_stimulus.py` + `Vitis/measure_sparse.py` | ✅ done 2026-08-26 |
| 19 | [S] | throughput sweep, one CSV per sparsity | ✅ **DONE 2026-08-27** — R^2 >= 0.9998 on all four |
| 20 | [S] | fanout experiment: 300 MHz + attributes + phys-opt | ✅ **FIX WORKS 2026-08-26** — broadcast no longer the critical path |
| 21 | [C] | comparison `.xlsx` from the dense + 4 sparse CSVs | ✅ **DONE 2026-08-27** — `results/GEMV_Hardware_Results.xlsx` |
| 22 | [C] | power tooling: `power_scraper.py`, `measure_power.py`, `[soak_seconds]` on both hosts | ✅ done 2026-08-27 |
| 23 | [S] | power measurement, 5 configurations | ⛔ **next** (waiting on the floorplan build) |
| 24 | [S] | SLR floorplan build (`slr_floorplan.cfg`) | ✅ **CLOSES 300 MHz 2026-08-31** |
| 25 | [S] | correctness ladder on the 300 MHz bitstream | ✅ **ALL 5 PASS 2026-08-31** |
| 26 | [S] | re-measure throughput at 300 MHz | ✅ **DONE 2026-08-31** |
| 27 | [S] | power, 5 configurations | ✅ **DONE 2026-08-31** |
| 28 | [C] | workbook with throughput + power, all clocks | ✅ **DONE** — `results/GEMV_Hardware_Results.xlsx`, **16 sheets** (225/300/325) |

**Step 7a result 2026-08-25 — PASS.** `--sparsity 10 --nwin 2 --nlaps 2`, full stress
(gaps 4/4, tready stall 0.30): **2 output beats = 128 rows**, matching 2:16 exactly
(M=16 → 32/16 = 2 beats/window × 2 windows × 2 laps = 8 weight beats; 2 laps × 64 rows).
`TKEEP ok on every output beat`, and `compare_gemv4_py36.py` reported PASS on both data and
TLAST. Three things proven at once: the wrapper adds no behaviour, the index TLAST it
discards is genuinely inert (the TB drives it deliberately), and the de-torched generator
matches the RTL bit-for-bit on real hardware stimulus.

**Step 4 evidence.** The de-torched generator was diffed against the torch original over
the whole verification ladder — all four sparsity codes, multi-window, multi-lap, runtime
reconfiguration (`--sparsities 00,01,10,11`), and two `--value-hi 100` cases where the
bf16 rounding actually bites. `weights.hex`, `indices.hex`, `activations.hex` and
`golden.txt` are **byte-identical in all seven**. Same result, same method, as the dense
generator at S19.

**Step 8 result 2026-08-25 — PASS. The sparse engine is re-runnable.** Two calculations,
no reset between them, A at 2:16/seed 111 and B at 2:4/seed 222: **256 rows bit-exact**
(128 + 128), end-of-calc markers on beats **[1, 3]** — the final beat of each calculation.

`compare_gemv4_py36.py` prints `TLAST : FAIL -- expected exactly 1 tagged beat, got 2` on
this testbench and **that is the correct outcome**: the script only models
single-calculation runs. The authoritative marker check here is the TB's own
`ends_seen /= 2 -> severity failure`, which passed. Do not "fix" the compare script for
this case — the single-run TBs depend on the strict one-marker rule.

**This is stronger evidence than the dense equivalent**, because the teardown also had to
clear the freeze counter across a sparsity change mid-stream (2 beats/window → 8).

**Step 9 partial result 2026-08-25.** `two2N_axis` OOC implementation: post-route
**WNS −0.096 ns, TNS −0.690 ns, 21 failing / 168,033 endpoints** at 2.222 ns, and
utilisation **CLB LUTs 62,417 / LUT as Logic 56,657 / Registers 79,464 / F7 Muxes 8,192 /
DSPs 512 / BRAM 74 / Bonded IOB 0**.

Two things to note. First, **the July `results/Utilization.xlsx` sparse row is a stale baseline** —
it predates the ingress stages, the 2:32 fix, the TLAST drain fix and the teardown. Against
it, timing is far better (−0.363 → −0.096 ns, 1,372 → 21 failing endpoints, Fmax ~387 →
~431 MHz) and LUTs are +47. Neither delta is attributable to the wrapper. Second, the
structural invariants are **exactly** on target: F7 Muxes 8,192 (the gather fingerprint:
64 blocks × 2 indices × 16 bits × 4 MUXF7), DSPs 512, BRAM 74, LUT as Memory 5,760.

**The gate is not closed until bare `two2N` is implemented on the CURRENT RTL** and
compared like-for-like. Prediction on record: it will also report 56,657 LUT as Logic and
79,464 registers, because `two2N_axis` contains only concurrent assignments — slicing,
concatenation and a constant `KEEP_ALL` — with no process and no conditional, so it cannot
synthesise to a LUT. That run also refreshes the sparse baseline, which the thesis area
table needs anyway (dense's is stale for the same reason).

**Step 9 FINAL 2026-08-25 — PASS. `two2N_axis` is transparent.** Both tops implemented
OOC on the SAME current RTL:

| Metric | bare `two2N` | `two2N_axis` | delta |
|---|---|---|---|
| CLB LUTs | 62,435 | 62,417 | −18 |
| LUT as Logic | 56,675 | 56,657 | −18 |
| CLB Registers | 79,504 | 79,464 | −40 |
| LUT as Memory | 5,760 | 5,760 | 0 |
| CARRY8 | 2,884 | 2,884 | 0 |
| F7 Muxes | 8,192 | 8,192 | 0 |
| DSPs | 512 | 512 | 0 |
| BRAM | 74 | 74 | 0 |
| Bonded IOB | 0 | 0 | 0 |
| WNS (2.222 ns) | −0.150 ns | −0.096 ns | — |

The prediction of *exact* equality was wrong; the wrapper is **18 LUTs and 40 FFs SMALLER**
than the bare engine. That is the direction that proves the point — a wrapper cannot shrink
a design by adding logic — and every structural invariant is exact. The residual (0.03%) is
tool variation: the wrapper exposes 544 unused `tkeep` input bits that get trimmed, so the
OOC boundary is not quite the same shape and synthesis decides marginally differently.
Do not chase it.

**REFRESHED SPARSE BASELINE (use this, not `results/Utilization.xlsx`'s July row):**

| | July | 2026-08-25 |
|---|---|---|
| LUT as Logic | 56,610 | **56,675** (+65) |
| CLB Registers | 79,462 | **79,504** (+42) |
| F7 Muxes / DSPs / BRAM | 8,192 / 512 / 74 | unchanged |
| WNS @ 450 MHz | −0.363 ns | **−0.150 ns** |
| Failing endpoints | 1,372 / 167,962 | **542 / 168,154** |
| Module Fmax | ~387 MHz | **~422 MHz** |

The +65 LUTs are the accumulated RTL fixes (teardown, ingress stages, 2:32 back-to-back,
TLAST drain). Timing improved substantially as a side effect — those fixes broke long
combinational paths.

⚠️ **THE DENSE BASELINE IS STALE FOR THE SAME REASON.** `dense_gemv` also gained the
teardown since July. One OOC implementation run of current-RTL `dense_gemv` is required
before the §7.1 area/Fmax table can go in the thesis; comparing a refreshed sparse number
against a July dense number would be wrong.

**Steps 12–14 done 2026-08-25.** `krnl_gemv_sparse.xo` built (132,234 bytes) and verified
by ARCHIVE CONTENT: all 11 VHDL files + `krnl_gemv_sparse.v`, all 7 IP `.xci`,
`component.xml` at 160,985 B (dense's was 136 KB for 14 interfaces — right scaling), and
**17 VIP `.xci`, one per interface**, which independently confirms all 17 streams exist with
the configured names.

Host-side pieces written and tested locally:

- **`GEMV_4.0_Source/Emulation/hex_to_bin.py`** — dense packer plus `indices.hex` → 3 PCs.
  Adds a check the dense one cannot have: the sparsity code must survive the PC split
  (`ind_pc2[129:128]`), since a wrong PC order would move or lose it while the plain
  round-trip still passed. `pack` prints the sparsity schedule it finds. Round-trip
  verified on single-sparsity and on the full `--sparsities 00,01,10,11` run.
- **`Vitis/host_sparse.cpp`** — 13 movers. **The one real design difference from dense:**
  dense derives `beats_per_lap = n_act_beats * 16`, a constant; sparse's is
  `n_act_beats * 32/M` and **M changes between laps** in a reconfiguration run, so
  `n_out_beats` cannot be obtained by division. The host instead reads the codes out of
  `ind_pc2.bin` and walks the laps exactly as `gemv4_cosim_gen.py` builds `beat_meta`, so
  host and golden model cannot disagree about lap boundaries. It also rejects a mid-lap code
  change and an index/weight beat-count mismatch (which would HANG the barrier join rather
  than give wrong numbers). Lap derivation verified against `golden.txt` row counts on four
  configurations including the 4-sparsity ladder.
- **`Vitis/sparse_hbm.cfg`** — `nk=krnl_mm2s:13`, 17 `sp=`, 17 `stream_connect=`.
  HBM map: weights 0–7, indices 8–10, activations 11–12, outputs 13–16.

Reporting note: the host counts index traffic as real input bandwidth and prints it as a
percentage, because the 8→11 PC step **is** the interface cost of sparsity and belongs in
the §7.1 comparison rather than a footnote. It also reports MAC rate twice — non-zeros
actually computed, and dense-equivalent `rows × V` — so the two architectures can be
compared on the same logical problem.

**Step 15a result 2026-08-25 — hw_emu PASS, Stage E closed for sparse.**
`v++ -t hw_emu` linked in **8m17s**; `cfgen` resolved 17/17 `stream_connect` and 17/17
`sp=`, and a grep for `dangling|unconnected|no matching|not found` over the link logs was
empty. Run: 18 CUs configured (`cus(18)`), 17 launched by the host, `all CUs done`,
**64 rows bit-exact** on `--sparsity 10 --nwin 1 --nlaps 1`.

Proven end to end: host → HBM → **13** mover CUs → AXIS → `two2N` → AXIS → 4 mover CUs →
HBM → host, through the real interconnect. Specifically including the three index channels,
the barrier join across all 11 weight+index PCs, and **the sparsity code read out of index
PC2 padding** — the engine derived 2:16 from buffer content that no host argument, mover or
link line knows about.

**IGNORE ALL hw_emu TIMING.** Profiling timestamps come from simulation time, so the kernel
span read 215 s for two beats. `v++` says so itself ("approximate models for global memories
and interconnect"). hw_emu proves correctness only; throughput is measured on the card.

**`tlast.txt not found` from the compare script is expected, not a coverage gap.** TLAST is
an AXIS sideband consumed by `krnl_s2mm`, so the host cannot observe it. Its correctness is
established in RTL simulation, where the TB sees the sideband directly. Dense is identical.

Mover `.xo` were **copied from `~/GEMV_Dense/Vitis/`, not rebuilt** — the mover kernels are
unchanged, and reusing the same binaries guarantees both designs are measured with identical
data movers, which the §7.1 comparison depends on.

**Step 15b 2026-08-26 — SPARSE DOES NOT CLOSE 300 MHz IN-SYSTEM. This is a RESULT.**
`v++ -t hw --kernel_frequency 300` completed in **3h19m41s** and wrote a 49.9 MB
`gemv_sparse.xclbin`, but post-route timing is **NOT met**:

| | sparse @ 300 | dense @ 300 |
|---|---|---|
| WNS | **−0.480 ns** | **+0.022 ns** |
| TNS | −737.465 ns | 0.000 ns |
| Failing endpoints | **3,266 / 873,629** | **0 / 821,238** |
| Achieved period | 3.813 ns | ≤3.333 ns |
| Achievable clock | **~262 MHz** | ≥300 MHz |

Not a marginal miss — half a nanosecond and 3,266 violating endpoints. **Vitis writes the
`.xclbin` anyway**; it is kept as `gemv_sparse_300fail.xclbin` and must NEVER be used for
measurement or for a correctness claim.

This is the in-system counterpart to the OOC finding (sparse module Fmax ~422 MHz vs dense
≥479) and it is a headline §7.1 number: **the gather costs clock frequency, and that partly
offsets the beat-count advantage.** Quote the in-system numbers, not the OOC ones — the
system adds 18 CUs and 17 HBM channels of routing pressure that OOC cannot see.

⚠️ **Establish WHICH clock domain owns the critical path before writing this up.** If it is
the gather/datapath, the finding is about the sparse architecture. If it is the HBM
interconnect, it is about fitting 17 channels on this device. Different claims — check
`Slack (VIOLATED)` in the routed timing report for the source/destination cells.

**Step 15c 2026-08-26 — 250 MHz also misses, but the HBM side is now clean.**
Link took **2h37m53s** (looser constraint = less router work).

| | 300 MHz | 250 MHz |
|---|---|---|
| kernel WNS | −0.480 ns | **−0.177 ns** |
| kernel TNS | −737.4 ns | **−53.9 ns** |
| kernel failing endpoints | 3,263 | **687** / 373,749 |
| `hbm_aclk` | −0.045, 3 failing | **+0.037, 0 failing** ✅ |

The 3 platform-side HBM endpoints resolved themselves with the new placement, so the
frequency problem is now purely our kernel clock.

**IMPORTANT METHODOLOGICAL POINT — do not compute "achievable Fmax" from a failed run.**
At 300 MHz the design achieved 3.813 ns; at 250 MHz it achieved **4.177 ns**. It got SLOWER
when the constraint was relaxed. That is normal timing-driven behaviour: the tools work as
hard as the constraint demands and no harder, so paths that were squeezed at 3.333 ns were
allowed to relax at 4.0 ns — and 687 of them relaxed past the line. Fmax must be established
by hitting a target that actually closes, not by dividing into a missed one. (The earlier
"~262 MHz" inferred from the 300 MHz run is therefore NOT a reliable figure.)

Next target **225 MHz** (4.444 ns) — 267 ps above what the 250 MHz run achieved with less
effort than 300 demanded. If that misses, go straight to 200 rather than trying 210: another
failed 3-hour cycle costs more than the 5% of clock it would protect.

**Step 15d/15e 2026-08-26 — SPARSE CLOSES AT 225 MHz AND RUNS BIT-EXACT ON THE U280.**

| Clock | WNS | TNS | Failing |
|---|---|---|---|
| `clkwiz_kernel_0` | **+0.048 ns** | 0.000 | **0** |
| `clkwiz_hbm_aclk_0` | +0.124 ns | 0.000 | 0 |

`gemv_sparse.xclbin` 49.7 MB. First hardware run (`--sparsity 10 --nwin 1 --nlaps 1`):
17 CUs launched, all completed, **64 rows bit-exact against golden.txt**.

**HEADLINE COMPARISON NOW MEASURED IN-SYSTEM: dense sustains 300 MHz, sparse 225 MHz — a
25% clock deficit**, with the mechanism identified (fo=128 activation broadcast, 93%
routing). Both closed with thin margin (dense +0.022, sparse +0.048).

**Reports snapshotted to `~/GEMV_Sparse/reports_225/`** — each new build wipes `_x/`, which
is how the 250 and 300 MHz reports were lost. Snapshot after EVERY build from now on.

**`host_sparse.cpp` took a 3rd argument: the clock the xclbin was LINKED at** (default 300).
It had 300 MHz hardcoded in `ideal_ns`, which would have understated `beats/cycle` and
`efficiency` by 25% on the 225 MHz build — silently, on exactly the numbers being quoted.
Dense runs pass nothing (300 is the default); sparse must pass `225`.

Ignore throughput from the tiny case: 460 us of kernel span for 2 beats is ~99.99% launch
overhead. Use the differential method (`measure.py`) on large cases.

**CRITICAL PATH IDENTIFIED 2026-08-26 — it is the ACTIVATION BROADCAST, and it is
ROUTING-dominated. This is a thesis finding, not a build detail.**

Per-clock breakdown of the failed 300 MHz build: **3,263 of 3,266 failing endpoints are on
the kernel clock** (WNS −0.480, TNS −737.375, 373,798 endpoints). The platform's 450 MHz
`hbm_aclk` has only **3** failing endpoints at −0.045 ns — negligible, but note it is NOT
affected by `--kernel_frequency`, so a lower-clock rebuild may still report "timing not met"
on those 3. Judge by the Intra Clock Table, not the headline verdict.

Worst path (5 paths tie at −0.480, then a smooth tail — a repeated structure, not one
unlucky net):

```
Source:      CORES_GEN[1]...C_CORE_REST/A_internal_reg[313]
Destination: CORES_GEN[4]...C_CORE[5].C_BLOCK_INST_REST/A_internal_reg[9]
Data Path Delay: 3.775 ns -- logic 0.323 ns (8.6%), ROUTE 3.452 ns (91.4%)
Logic Levels: 3  (LUT6=2, MUXF7=1)
```

**The gather logic costs 323 ps; the wire costs 3.45 ns.** Three logic levels only. This path
is the shared activation broadcast between cores, not the MAC datapath.

**Why sparse pays this and dense does not:** sparse needs the whole 32-element window visible
to *every* block, because any block's index may select any element. Dense uses elements 2c
and 2c+1 at beat c — a fixed position — so its activation distribution is local and short.
**The real cost of the gather is the broadcast network it forces, not the mux LUTs.** The
8,192 F7 muxes are the visible symptom; 3.45 ns of routing is the price.

Likely aggravating factor: a source in core 1 driving a destination in core 4 suggests
Vivado **merged** the per-core `A_internal` registers (identical data, same enable) into one
physical set fanning out to all 64 blocks. Consistent with the known attribution artifact
where all 8,192 F7 muxes were charged to `CORES_GEN[0]`. The clock path also shows an SLR
crossing.

**FUTURE-WORK EXPERIMENT (not now):** `KEEP` / `DONT_TOUCH` on each core's `A_internal` to
force 8 separate copies and cut fanout ~8x. A synthesis attribute, not a functional change,
so it does not disturb the verified datapath. Could recover part of the frequency gap. Worth
mentioning in the thesis as future work whether or not it is run.

**Step 10 note — the wizard's captured Tcl omits `AXISnn_MODE` on 7 of 17 slots.** Not a
bug: the wizard default alternates `write_only` (even index) / `read_only` (odd), and
Vivado echoes only properties that DIFFER from default. Every omitted slot happened to
match its default. `gen_xo_sparse.tcl` sets all 17 modes **explicitly** regardless — the
default alternation means the same GUI choices emit a different Tcl at 17 streams than at
14, so replaying a capture would silently flip stream directions.

**Step 6 note — why the rerun TB switches sparsity between A and B.** The sparse teardown
clears more than the dense one (the freeze counter and its window bookkeeping, not just
the replay FIFO). If both halves ran at the same cadence a leftover freeze count would be
invisible. Generating B at a different sparsity costs nothing and tests strictly more:

```
python3 gemv4_cosim_gen.py --sparsity 10 --nwin 2 --nlaps 2 --seed 111   # A: 2:16
python3 gemv4_cosim_gen.py --sparsity 00 --nwin 2 --nlaps 2 --seed 222   # B: 2:4
```
(rename each set to `*_a.*` / `*_b.*` between runs, then
`cat golden_a.txt golden_b.txt > golden.txt`).

**Server paths (confirmed 2026-08-25):** `ls ~ | grep -i gemv` returns `GEMV_Dense` and
`GEMV_Sparse` — note the sparse ROOT is `GEMV_Sparse`, NOT `GEMV_4.0`, so the server root
name does not simply mirror the repo folder the way dense does. Both new testbenches use
`/home/skoulas/GEMV_Sparse/GEMV_4.0_Source/Emulation/` in a single `SPARSE_ROOT` / `DIR`
constant.

### S24 [S] ✅ **DONE 2026-08-26** — hw_emu, then hardware, 2:16

### S25 [S] ✅ **PASSED 2026-08-26** — the full sparsity ladder on hardware

`Vitis/run_ladder.sh`, `--nwin 4 --nlaps 4`, 225 MHz bitstream. **All bit-exact:**

| Sparsity | Freeze (32/M) | Weight beats | Output rows | Result |
|---|---|---|---|---|
| 2:4 | 8 | **128** | 256 | 0 mismatches |
| 2:8 | 4 | **64** | 256 | 0 mismatches |
| 2:16 | 2 | **32** | 256 | 0 mismatches |
| 2:32 | 1 | **16** | 256 | 0 mismatches |

**Identical output for 8x fewer beats**, halving exactly at each step. This is the core
architectural claim, measured on silicon rather than argued.

### S26 [S] ✅ **PASSED 2026-08-26** — the mixed runtime-reconfiguration case

`--sparsities 00,01,10,11 --nwin 4`: ONE calculation, activation vector loaded once, four
laps at 2:4 -> 2:8 -> 2:16 -> 2:32. **60 weight beats (32+16+8+4), 256 rows, 0 mismatches.**

The freeze cadence changed three times mid-calculation with **no reload and no host
involvement** — the engine re-samples the 2-bit code from index PC2 padding at every window
load. The host never sends a sparsity argument; it only reads the codes back out of
`ind_pc2.bin` to predict lap boundaries.

**This is the architecture's headline claim and it now has hardware evidence.**

*(The §1.2 core-count re-verification that used to sit here is out of scope — see Scope.)*

**STAGE G COMPLETE 2026-08-26.** Sparse is verified end to end on the physical U280:
wrapper transparent, `.xo` packaged, hw_emu bit-exact, timing closed at 225 MHz, and all
five hardware cases bit-exact including runtime reconfiguration. The same Python golden
model validated behavioural sim -> post-impl sim -> hw_emu -> hardware for BOTH
architectures, unchanged throughout.

**What remains for the comparison:**
1. **Throughput at each sparsity** — tooling is WRITTEN and locally verified
   (`GEMV_4.0_Source/Emulation/gen_timing_stimulus.py`, `Vitis/measure_sparse.py`); the
   sweeps have not been run. Quote sparse at 225 MHz and dense at 300, and report BOTH raw
   Mrow/s and clock-normalised beats/cycle — the clock deficit is part of the result, not
   something to normalise away, but beats/cycle is what isolates the architecture from the
   frequency it happened to close at.
2. **Dense area/timing baseline refresh** — the July `results/Utilization.xlsx` dense row predates
   the teardown, so it cannot be compared against the refreshed sparse numbers. One OOC
   implementation run of current-RTL `dense_gemv`.
3. **The `A_internal` fanout experiment** — see S26b. Running as of 2026-08-26. **The §7.1
   frequency row stays provisional until it lands**: "sparsity costs 25% of the clock" and
   "sparsity costs nothing once the broadcast is replicated" are materially different
   claims, and publishing the first then retracting it would be worse than waiting.

---

---

## Stage H — the sweeps (one variable per `.xclbin`)

### S26b [C+S] 🔄 **IN FLIGHT 2026-08-26** — the activation-broadcast fanout experiment

Not in the original plan. Added because the 300 MHz failure had a single identified cause
worth attacking rather than working around. **Three changes, all inert in simulation:**

1. `c_core_4.0.vhd` — `equivalent_register_removal = "no"` on `A_internal`, stopping Vivado
   collapsing the 8 identical per-core window registers into one (measured fo=128 = 64
   blocks x 2 gather muxes).
2. same file — `max_fanout = 16`, forcing further replication if any copy still exceeds one
   core's worth of loads.
3. `Vitis/impl_opt.cfg` (NEW, separate from `sparse_hbm.cfg` on purpose) — `phys_opt_design`
   and `POST_ROUTE_PHYS_OPT_DESIGN` at `AggressiveExplore`. Post-route phys-opt can
   replicate high-fanout drivers with real routing delays in hand, so it attacks the same
   problem from the tool side, plus the `s2mm_c2` congestion.

Held in reserve if this is not enough: `PLACE_DESIGN=ExtraTimingOpt`,
`ROUTE_DESIGN=AggressiveExplore` (roughly double the build), and SLR floorplanning
(`slr=gemv:SLR1` — real, but it trades broadcast delay for 17 stream crossings and could
easily backfire).

Running at **300 MHz deliberately**: 225 is banked as the safety net, so this build's job is
information — does removing the broadcast bottleneck close the gap to dense outright? Even a
failure yields a WNS that quantifies what the fix bought.

**PRACTICAL RULES LEARNED, do not relearn them:**
- **Two concurrent `v++` links MUST use different working directories.** `_x/` is
  per-directory; two writers corrupt each other with no error. The fix build lives in
  `~/GEMV_Sparse/Vitis_fix/`.
- **`gen_xo_sparse.tcl` now takes `set ::GEN_XO_TAG _fix`** before sourcing, to write
  `xo_build_fix/` and `krnl_gemv_sparse_fix.xo`. Sourcing it without the tag overwrites the
  baseline `.xo` — which happened, 7½ minutes after the 225 build had extracted it. Harmless
  only by luck; `system_link` extracts once at the start and never re-reads.
- **The `.xo` is a SEALED COPY of the VHDL.** An RTL change is a two-stage rebuild: `.xo`
  (~20 min) then link (~3 h). Verify the change actually travelled:
  `unzip -p <xo> ip_repo/.../src/c_core_4.0.vhd | grep max_fanout`
- **SNAPSHOT REPORTS AFTER EVERY BUILD.** `_x/` is wiped at the start of the next link; the
  300 and 250 MHz timing reports were lost that way. `~/GEMV_Sparse/reports_225/` exists;
  do the same for every subsequent build.

---

**S26b RESULT 2026-08-26 — THE FANOUT FIX WORKS. The broadcast is no longer the limiter.**
Build took 3h51m33s (phys-opt adds time). Still fails 300 MHz, but the failure has moved.

⚠️ **READ THE RIGHT REPORT.** With `POST_ROUTE_PHYS_OPT_DESIGN` enabled, `..._routed.rpt`
is the PRE-phys-opt snapshot; the final numbers are in
`..._timing_summary_postroute_physopted.rpt`. Baseline builds had no such stage, so `routed`
was final for them. Comparing the wrong pair understates the fix by 346 ps.

| | 300 baseline (final) | 300 + fix (routed) | 300 + fix (FINAL, post-phys-opt) |
|---|---|---|---|
| Worst path | `A_internal` bcast, **fo=128** | `mm2s_w2`, **fo=7** | — |
| Logic levels | 3 (2x LUT6 + MUXF7) | **1** (LUT6) | — |
| Kernel WNS | −0.480 ns | −0.645 ns | **−0.299 ns** |
| Kernel TNS | −737.4 ns | −308.5 ns | **−138.8 ns** (5.3x better) |
| Failing endpoints | 3,263 | 1,506 | **1,201** (2.7x better) |
| `hbm_aclk` | −0.045, 3 failing | +0.023, 0 failing | +0.023, 0 failing |

**The fix improves EVERY metric.** Post-route phys-opt alone recovered a further 346 ps.

New worst path: `mm2s_w2/inst/ap_CS_fsm_reg[71]` -> `.../rs_rdata/data_p1_reg[228]/CE`,
3.955 ns of which **logic is 0.116 ns and route is 3.839 ns**, with **ONE logic level** and
a **fanout-7** net contributing 3.079 ns. One LUT, seven loads, three nanoseconds of wire:
that is two registers placed far apart, not a logic or fanout problem.

**SLR SPREAD MEASURED (`impl_1_slr_util_routed.rpt`): the design spans ALL THREE dice.**

```
SLR2 <-> SLR1 : 3,995 SLLs
SLR1 <-> SLR0 : 2,554 SLLs
Total SLLs    : 6,549
```

The largest traffic is SLR2<->SLR1 — i.e. a big chunk sits in SLR2, the die FURTHEST from
HBM (all 32 HBM channels are in SLR0). Nothing forces this: gemv is ~62k LUTs against ~400k
per SLR, and the 17 HLS movers are small. SLR0+SLR1 would suffice.

**The structural argument for floorplanning:** the 17 kernel-to-kernel links are AXI4-Stream
— handshake-based and latency-tolerant, so a die crossing costs them almost nothing. What
crosses today is whatever the placer happened to split, including timing-critical nets.
Deliberate assignment puts the tolerant traffic on the SLL boundary and keeps critical logic
local:

```ini
slr=gemv:SLR1
slr=mm2s_w0:SLR0   # ... all 13 movers
slr=s2mm_c0:SLR0   # ... all 4 writers
```

**THESIS CLAIM, SHARPENED.** Not "sparse is 25% slower because the gather is slow" but:
*the gather's 64-way activation broadcast was the limiter; replicating it per core removes
it, after which the design is limited by global placement spread across 18 CUs and 17 HBM
channels — a system-integration cost, not an architectural one.* Before/after reports are
in `~/GEMV_Sparse/reports_225/` and `~/GEMV_Sparse/reports_300fix/`.

**The next lever is therefore FLOORPLANNING, not RTL.** A 1-logic-level path spending 97%
of its time on wire is a placement statement. SLR assignment (`slr=` in the link config) and
`PLACE_DESIGN=ExtraTimingOpt` are the indicated moves; further fanout work is not.

---

### S27 ✅ **MEASURED 2026-08-27** — §7.1 architecture: dense vs sparse × 4 modes

Marginal (launch-overhead-free) least-squares fits. **Identical method on both sides**:
four problem sizes spanning the SAME 262,144 -> 2,097,152 weight-beat range, best-of-N
kept, R^2 >= 0.9998 everywhere. Sparse ran 15 reps per size, dense 5.

| | clock | beats/row | Mrow/s | vs dense | GB/s | beats/cycle |
|---|---|---|---|---|---|---|
| dense | 300 MHz | 8 | 36.99 | 1.00x | 75.83 | 0.986 |
| **2:4** | 225 MHz | 4 | **56.03** | **1.51x** | 79.01 | 0.996 |
| **2:8** | 225 MHz | 2 | **109.87** | **2.97x** | 77.57 | 0.977 |
| **2:16** | 225 MHz | 1 | **220.35** | **5.96x** | 78.00 | 0.979 |
| **2:32** | 225 MHz | 0.5 | **431.13** | **11.66x** | 76.74 | 0.958 |

**Measured against theory: 101% / 99% / 99% / 97%.** At equal clock the speed-ups are
2.02x / 3.96x / 7.94x / 15.54x against a theoretical 2 / 4 / 8 / 16.

**Three findings.**

1. **Throughput doubles exactly per sparsity step** — ratios 1.96 / 2.01 / 1.96, and 7.70x
   across the full ladder against an ideal of 8x = **96% of theoretical**.
2. **Cost per weight beat is CONSTANT**: 0.004462 / 0.004551 / 0.004538 / 0.004639 us — a 4%
   spread over a 4x sparsity range. The engine consumes one weight beat per cycle regardless
   of sparsity; sparsity only changes how many beats a row costs. **This is the
   architecture's central claim, measured rather than argued.**
3. **Sparse beats dense at EVERY ratio, including 2:4, despite a 25% lower clock.** The
   index overhead (3 extra PCs, 37.5% more bytes per beat) is more than repaid by halving
   the beats.

At 2:4 the engine reaches **79.01 GB/s against a hard ceiling of 79.2 GB/s** (11 input PCs
x 32 B x 225 MHz) — 99.8% of the input channels.

**Data moved per row**, the interface cost of sparsity: dense 2048 B; sparse 1408 / 704 /
352 / 176 B = **68.8% / 34.4% / 17.2% / 8.6%** of dense. Index traffic is 27.3% of sparse
input bytes at every ratio.

> ⚠️ **A HYPOTHESIS THAT WAS WRONG, recorded so it is not repeated.** An earlier, noisier
> sweep (smaller sizes, 5 reps, spreads of 30-60%) showed beats/cycle apparently falling
> with sparsity — 0.969 / 0.831 / 0.879 / 0.787 — and I attributed it to the output path
> saturating at 2:32, where the freeze is 1 cycle and a flush occurs every cycle. **That was
> measurement noise, not an effect.** Re-measured properly it is 0.996 / 0.977 / 0.979 /
> 0.958: essentially flat and near roofline. **There is no output-path bottleneck.** The
> The real tell was the SPREAD (30-60% against 4-5% in the good data), not the impossible
> beats/cycle values — see the scoping note below.
>
> ⚠️ **CORRECTION 2026-09-02.** An earlier version of this paragraph said three differential
> rows above 1.0 beats/cycle "should have been the tell". **That was overstated.** A scan of
> all nine throughput CSVs found **six differential rows above the roofline at 225 and 300
> MHz in data whose FITS are all sound** (0.958–0.996 beats/cycle, R² ≥ 0.9988). Differentials
> subtract two launch overheads that are only approximately equal, so a hot differential is
> routine noise, not evidence of a bad sweep. **What disqualifies a sweep is the FIT row going
> above 1.0** — which has happened exactly once (2:4 at 325 MHz, rejected in S27h).

Full workbook at the time of S27a: **`results/GEMV_Hardware_Results.xlsx`** (Comparison,
Methodology, five raw-data sheets), built from `results/dense_300MHz.csv` +
`sparse_2to{4,8,16,32}_225MHz.csv`. ⚠️ **SNAPSHOT — SUPERSEDED.** The workbook has since
grown to **16 sheets** covering 225, 300 and 325 MHz plus power at two clocks; see S27e and
S27h for the current contents.

---

### S27b — the ORIGINAL S27 plan text, retained

Already collected by S22 + S25. Also compute **bytes moved** — sparse carries indices
(2048 + 640 b vs dense's 2048, a 31 % per-beat overhead), so net data moved is
`(2/M) × 21/16` → 66 / 33 / 16 / 8 %. The 2:4 asymmetry (2× faster, only 34 % less data)
is the most interesting single number in the thesis.

### S27c [C+S] 🔄 **TOOLING DONE 2026-08-27, measurement pending** — power and energy

Added at the professor's request; not in `INTEGRATION_PLAN.md`. His reference implementation
was a `power_scraper` around `xbutil examine --report electrical`, plus a pyJoules CPU side.

**What was taken from it, and what was not.** The `xbutil` call is right and is used. The
parser was rewritten **key-based instead of line-indexed** — the reference reads `lines[5]`,
`[6]`, `[7]` and everything past index 9, which happens to match XRT 2.13 here but would
silently turn "Max Power" into a voltage if a header line moved. The **CPU/pyJoules half was
dropped**: in the reference it is a stub returning a hardcoded 12.3 W, and more importantly
host power cannot discriminate dense from sparse — the host does identical work in both.

**What the U280 actually reports** (XRT 2.13, verified 2026-08-27): 14 rails, but only THREE
carry a current reading and therefore yield power:

```
12 Volts Auxillary    12.308 V x 1.511 A = 18.60 W
12 Volts PCI Express  12.337 V x 1.382 A = 17.05 W
Internal FPGA Vcc      0.851 V x 11.310 A = 9.61 W   <- VCCINT
reported "Power"                          = 35.647 W
```

18.60 + 17.05 = 35.65 = the reported Power exactly. So **board power is the two 12 V input
rails**, and **VCCINT is a SUBSET of it** downstream of the regulators — never additive.
Report both: board is what the machine draws; VCCINT is closest to what the design costs,
with HBM and transceiver draw (identical between designs) largely excluded.

**Method, and why it is not "run the host and read the meter".** One calculation lasts
10-80 ms while `xbutil examine` takes ~1 s — a 1 Hz instrument cannot sample a 10 ms window.
Worse, a plain run is ~85% H2D transfer (74.7 ms DMA against a 10.2 ms kernel at the largest
size), so most samples would land on the DMA engine. So both hosts gained an optional
**`[soak_seconds]`** argument: re-enqueue all CUs back-to-back for N seconds with buffers
already resident — no transfers — and print the window as `SOAK_START_EPOCH` /
`SOAK_END_EPOCH` so the sampler clips to exactly the load. Default behaviour is unchanged
when the argument is omitted.

The idle baseline comes free: after the host exits the bitstream stays programmed, so
sampling on gives idle power FOR THE SAME DESIGN, holding leakage and clock trees constant.

**Two things the 5 s trial established:**
- **The card's telemetry LAGS the load.** A sample taken just after the soak began read
  28.94 W, BELOW the 29.30 W idle mean. Hence `--warmup 6`, which discards the start of the
  load window. Do not set it to 0.
- **The soak keeps the accelerator ~96% fed even with a Vivado build running** (414 Mrow/s
  sustained against a 431 fitted marginal rate), so CPU contention is not the threat I
  expected.

**The dynamic delta is small and must be quoted with its error**: ~3 W board on a ~29 W
idle, under a ~4 W sample-to-sample spread. The mean is sound (standard error falls as
1/sqrt(n); ~55 samples after warm-up gives a few hundred mW) but the spread has to travel
with it. Standard errors are computed and printed.

**Do NOT use the pre-run reading as a baseline.** It is taken before the host reconfigures
the FPGA, so it reflects whatever design was previously loaded — in the trial it read
34.91 W, HIGHER than our design under load.

Files: `Vitis/power_scraper.py`, `Vitis/measure_power.py`, `[soak_seconds]` on both
`Vitis/host.cpp` and `Vitis/host_sparse.cpp`.

**Measurement is pending** — deliberately deferred until the floorplan build finishes, so
all five configurations run back-to-back under identical machine conditions. A systematic
difference BETWEEN configurations is the one thing that would invalidate the comparison.

---

### S27d [S] ✅ **2026-08-31 — SPARSE CLOSES 300 MHz. THE CLOCK DEFICIT IS GONE.**

`slr=gemv:SLR1` + all 17 movers on `SLR0`, on top of the `A_internal` fanout attributes.
Build 3h25m52s, `gemv_sparse_slr.xclbin` 53.8 MB.

| build | delta from previous | kernel WNS | TNS | failing | closes |
|---|---|---|---|---|---|
| 300 baseline | — | −0.480 ns | −737.4 | 3,263 | no |
| 300 + fanout fix | 2 synthesis attributes | −0.299 ns | −138.8 | 1,201 | no |
| 225 | lower target only | +0.048 ns | 0 | 0 | yes |
| **300 + fix + floorplan** | `slr=` assignments | **0.000 ns** | **0** | **0** | **YES** |

`hbm_aclk` +0.082, 0 failing. **Same RTL throughout** apart from the two attributes — the
rest was placement.

**THE FLOORPLAN DID EXACTLY WHAT IT WAS DESIGNED TO DO**, and the numbers prove the
mechanism rather than merely correlating with it:

| CLB LUTs | SLR0 | SLR1 | SLR2 |
|---|---|---|---|
| before | 134,875 | 47,669 | 35,352 |
| after | **69,453** | **113,940** | 35,362 |
| delta | **−65,422** | **+66,271** | +10 |

`gemv` is **66,186 LUTs** — it moved wholesale SLR0 → SLR1, matching its size. SLR1↔SLR0
crossings went 2,554 → **6,853 (+4,299)**, against a prediction of ~4,450 for the 17 AXIS
streams (17 x ~262 wires). **Adding 4,299 crossings IMPROVED timing**, because they are all
handshake-based AXI4-Stream and therefore latency-tolerant — which was the whole argument
for the assignment.

**SLR2 was never ours.** Its 35,362 LUTs plus ~41,600 in SLR0 and ~47,750 in SLR1 sum to
~124,700 against the platform's 123,863 — the shell and HBM subsystem span all three dice
and cannot be moved. The 3,993 SLR2↔SLR1 crossings are platform-internal.

**CORRECTNESS VERIFIED 2026-08-31** before any number was quoted: `run_ladder.sh` at
300 MHz, all five configurations **bit-exact**, including runtime reconfiguration.

⚠️ **WNS is exactly 0.000 ns** — a genuine pass (0 failing endpoints) but with no margin,
against dense's +0.022. Worth stating as such rather than as comfortable closure.

⚠️ **CONSEQUENCE: the 225 MHz throughput numbers are now a SECOND data point, not the
headline.** Throughput and power must both be re-measured on this bitstream. The 225 MHz set
stays as the record of what floorplanning bought (a 33% clock recovery), but the §7.1
comparison should be dense@300 vs sparse@300 — at equal clock the speed-up is purely the
beat-count ratio, with no clock term to explain away.

**PREDICTION for the 300 MHz sweeps** (recorded before measuring): the 225 MHz fits scale by
300/225 = 1.333 to **74.7 / 146.5 / 293.8 / 574.8 Mrow/s**, i.e. **2.02x / 3.96x / 7.94x /
15.54x** dense — which should land on the theoretical 2 / 4 / 8 / 16.

**PRACTICAL: `run_ladder.sh` now refuses to start unless `XILINX_XRT` is set.** Without it
the host does not fail gracefully — XRT throws `std::runtime_error`, nothing catches it, and
the process dies with `Aborted (core dumped)`, which looks like a design fault. It cost five
identical failures before the cause was clear. XRT is per-shell and does not survive a new
terminal, tmux window or reconnect.

---

### S27e ✅ **MEASURED 2026-08-31** — throughput and power at EQUAL CLOCK (300 MHz)

Both designs at 300 MHz, so the comparison carries no clock term.

**THROUGHPUT** (marginal fits, identical 262,144 → 2,097,152 beat range):

| | beats/row | Mrow/s | vs dense | theory | % of theory | beats/cyc | us/beat |
|---|---|---|---|---|---|---|---|
| dense | 8 | 36.99 | 1.00x | — | — | 0.986 | 0.003379 |
| 2:4 | 4 | **73.40** | **1.98x** | 2x | 99.2% | 0.979 | 0.003406 |
| 2:8 | 2 | **146.44** | **3.96x** | 4x | 99.0% | 0.976 | 0.003414 |
| 2:16 | 1 | **290.73** | **7.86x** | 8x | 98.3% | 0.969 | 0.003440 |
| 2:32 | 0.5 | **578.16** | **15.63x** | 16x | 97.7% | 0.964 | 0.003459 |

**Cost per weight beat is CONSTANT AND MATCHES DENSE within 2.4%** across a 4x sparsity
range and across two architectures. That is the central claim in its strongest form: the
engine costs the same per beat whatever the design or sparsity; sparsity only changes how
many beats a row needs.

Prediction recorded before measuring (225 fits x 1.333) held to within 1.5% on all four.
Replicates: 2:8 agrees to 0.4%, 2:16 to 1.4% across independent runs.

**POWER** (60 s soak, buffers resident, ~54 samples after a 6 s warm-up trim):

| | board load | board idle | board dyn | VCCINT load | VCCINT idle | VCCINT dyn |
|---|---|---|---|---|---|---|
| dense | 31.30 | 26.92 | 4.37 | 7.73 | 6.66 | **1.07** |
| 2:4 | 35.44 | 30.14 | 5.30 | 7.76 | 7.10 | 0.66 |
| 2:8 | 35.33 | 30.12 | 5.21 | 7.82 | 7.17 | 0.65 |
| 2:16 | 35.41 | 30.23 | 5.19 | 7.91 | 7.17 | 0.74 |
| 2:32 | 35.52 | 30.14 | 5.38 | 7.91 | 7.25 | 0.66 |

**ENERGY EFFICIENCY — the result:**

| | nJ/row (board) | vs dense | nJ/row (VCCINT) | vs dense | GMAC/s/W |
|---|---|---|---|---|---|
| dense | 887.5 | 1.00x | 219.2 | 1.00x | 1.15 |
| 2:4 | 508.0 | 1.75x | 111.2 | **1.97x** | 2.02 |
| 2:8 | 253.7 | 3.50x | 56.2 | **3.90x** | 4.04 |
| 2:16 | 128.4 | 6.91x | 28.7 | **7.65x** | 7.98 |
| 2:32 | **65.1** | **13.63x** | **14.5** | **15.12x** | **15.72** |

**Three findings, and each is easy to misread:**

1. **SPARSE POWER IS FLAT ACROSS SPARSITY** — board load 35.33-35.52 W at every ratio. Not a
   null result: it IS the mechanism. One weight beat per cycle regardless of sparsity means
   the same watts regardless of sparsity, so energy per row falls exactly as throughput
   rises (509 → 253.7 → 128.4 → 65.1, ratios 2.01 / 1.98 / 1.97).
2. **SPARSE PAYS ~3.2 W OF STATIC OVERHEAD** — idle 30.14 W against dense's 26.92, for 66k
   LUTs against 44k and 17 HBM channels against 14. Paid whether it works or not, and it is
   why the board-level gain (13.6x) trails the VCCINT gain (15.1x) and the throughput gain
   (15.5x). The board figure is the honest one for deployment.
3. **On VCCINT the energy gain tracks the throughput gain almost exactly** (1.97/3.90/7.65/
   15.12 against 1.98/3.95/7.82/15.47). On the FPGA core, sparsity converts to energy
   efficiency one-for-one. **Always say which rail you mean** — VCCINT is a SUBSET of board
   power, never additive.

⚠️ **A HYPOTHESIS, NOT A FINDING.** Dense's VCCINT *dynamic* power is **1.07 W against
sparse's ~0.66 W** — dense burns MORE core power while working, despite both doing 128 MACs
per cycle. Plausible mechanism: sparse freezes its 512-bit activation window for 32/M cycles,
so those registers toggle up to 8x less often, while dense's advances every beat. Nothing
measured here isolates that. Do not present it as established.

**PREDICTION THAT WAS WRONG, recorded for honesty.** I predicted dense would draw ~35.4 W
like sparse, on the reasoning that power is set by beat rate. It drew **31.30 W**. The error
was ignoring that these are different bitstreams with different STATIC power — the ~3.2 W
idle gap above. The throughput half of the prediction (~35 Mrow/s) was right (35.265).

Workbook: **`results/GEMV_Hardware_Results.xlsx`**, **16 sheets** — Comparison, Clock study, Power
and energy, power_raw, **325 MHz**, **power_raw_325**, Methodology, and nine raw sheets.
Built by `make_comparison_xlsx.py` from the CSVs in `results/`. The two 325 sheets were
added by S27h.

---

### S27f ✅ **2026-09-01 — BOTH DESIGNS CLOSE 325 MHz, both bit-exact**

| | WNS @300 | WNS @325 | build | hbm_aclk @325 |
|---|---|---|---|---|
| dense | +0.022 | **+0.013** | 2h57m | +0.027 |
| sparse | 0.000 | **+0.001** | 4h57m | +0.132 |

Zero failing endpoints on both clocks in both designs. **Correctness verified at V=1024**
(`run_ladder.sh ... 325 32 8`): sparse bit-exact at all four sparsities AND runtime
reconfiguration; dense bit-exact on 512 rows. Deliberately run at the measurement's actual
vector length rather than the 128-element default, because these are ~zero-margin designs.

Note sparse got *marginally better* margin at the tighter constraint (0.000 → +0.001) — the
same "tools work exactly as hard as the constraint demands" effect seen at 250 vs 300. Its
real ceiling is >= 325.

**Levers used: implementation strategy (`Performance_ExplorePostRoutePhysOpt` +
`AggressiveExplore` routing) and the SLR floorplan. NOT the synthesis strategy.**

⚠️ **`--vivado.prop run.synth_1.*` IS SILENTLY IGNORED IN THIS FLOW.** `v++ -l` builds the
dynamic region as a DFX reconfigurable module, so the Vivado runs are `my_rm_synth_1` and
`impl_1` — a property addressed to `synth_1` matches no run and produces NO warning.
Confirmed by reading the command line out of `_x/logs/link/vivado.log`:

```
synth_design -top pfm_dynamic -part xcu280-fsvh2892-2L-e -mode out_of_context
```

No `-directive`, no `-fsm_extraction`, no `-keep_equivalent_registers`. Meanwhile
`-directive Explore` x4 + `-directive AggressiveExplore` x1 confirm the implementation
properties DID apply.

**Consequences:** (a) `Flow_PerfOptimized_high` is still UNTESTED and remains an unspent
lever; (b) the §7.1 AREA comparison is unaffected — `-resource_sharing off` and `-no_lc`
would have inflated LUT counts, and they never ran.

**ALWAYS VERIFY A `--vivado.prop` LANDED.** Absence of an error is not evidence it applied.

---

### S27g ✅ **2026-09-02 — Fmax ESTABLISHED: dense 350 MHz, sparse 325 MHz**

Both designs built at 350 with `Flow_AlternateRoutability` synthesis (the FIRST build in which
a synthesis strategy actually applied) on top of the unchanged implementation strategy and
floorplan.

| target | dense | sparse |
|---|---|---|
| 300 | +0.022 ✓ | 0.000 ✓ |
| 325 | +0.013 ✓ | +0.001 ✓ |
| **350** | **+0.025 ✓** | **−0.204 ✗** (TNS −42.9, 705 endpoints) |

**DENSE: 350 MHz. SPARSE: 325 MHz.** The sparse failed run's achieved period is 3.061 ns
≈ 327 MHz, which corroborates the 325 that closed. (Still an estimate — only a closing
constraint establishes Fmax; see the 300-vs-250 counterexample recorded earlier.)

**`Flow_AlternateRoutability` WORKS, and the attribution is clean.** Dense went +0.013 at 325
to **+0.025 at 350** — MORE margin at a 25 MHz tighter constraint. The synthesis strategy was
the only change between those two builds. Choosing it over `Flow_PerfOptimized_high` was
right on the evidence: the worst path was ONE LUT6 with fanout 7 spending 97% of its time on
wire, i.e. a routability problem, not a logic-depth one.

`Flow_AlternateRoutability` expands to `-directive AlternateRoutability -no_lc
-shreg_min_size 10`. Confirmed applied by reading the command line:
`synth_design -top pfm_dynamic ... -directive AlternateRoutability -no_lc -shreg_min_size 10`.
**The working run name is `my_rm_synth_1`, NOT `synth_1`.**

**THE RESIDUAL COST OF THE GATHER IS ~7% OF CLOCK.** It began as 25% (dense 300 vs sparse
225). Per-core replication of the activation-window register and the SLR floorplan removed
most of it; 350-vs-325 is what is left. That is the honest final statement on frequency.

**WHAT THIS DOES AND DOES NOT CHANGE:**
- The measured throughput/power comparison is at **300 MHz on both designs** and remains
  complete, self-consistent and correct. Nothing here disturbs it.
- 325 MHz is verified correct on both designs AND **now measured for power/energy on all five configurations** (2026-09-02, `results/power_results_325.csv`, workbook sheet
  "325 MHz"). See S27h below.
- 350 MHz is a dense-only Fmax result. **Do not build an equal-clock comparison on it.**
- Reporting both is honest and complementary: *at equal clock sparse is 1.98/3.96/7.86/15.63x
  faster; dense reaches a 7% higher maximum clock.*

⚠️ If 325 or 350 ever becomes the measured headline, the §7.1 AREA table must be rebuilt with
BOTH designs under the same synthesis strategy — at 350 `-no_lc` and `-shreg_min_size 10`
applied, at 300/325 no synthesis strategy applied at all.

---

### S27h ✅ **2026-09-02 — 325 MHz measured: power/energy on all five, scaling confirmed**

Five 60 s soaks on the 325 MHz bitstreams (`results/power_results_325.csv`). **Board power is flat
across sparsity again — 35.45–36.34 W, a 2.5% spread across a 16× throughput range** —
reproducing the 300 MHz mechanism. Static gap unchanged at **3.14 W** idle (sparse 30.21 vs
dense 27.08); leakage does not care about frequency, and dynamic power duly rose in both.

Energy per row: dense 832.8 nJ; sparse 474.1 / 236.0 / 121.3 / **61.4** nJ → up to
**13.55× better** on board, **15.04×** on VCCINT. The sparse-vs-dense energy ratio at 2:4 is
1.757× here against 1.747× at 300 — clock-independent, as expected.

**THROUGHPUT SCALES LINEARLY WITH CLOCK.** Soak-vs-soak, 300 → 325:

| | 300 | 325 | measured | ideal 1.0833 |
|---|---|---|---|---|
| dense | 35.27 | 38.09 | 1.0802 | −0.29% |
| 2:4 | 69.77 | 75.92 | 1.0881 | +0.44% |
| 2:8 | 139.27 | 150.24 | 1.0788 | −0.42% |
| 2:16 | 275.86 | 297.45 | 1.0783 | −0.47% |
| 2:32 | 545.42 | 591.37 | 1.0843 | +0.09% |

Every config within 0.47% of ideal. Soak-based speed-ups vs dense hold at both clocks:
1.979/3.949/7.823/15.466 at 300 vs **1.993/3.944/7.808/15.524** at 325.

⚠️ **NO MARGINAL FITS AT 325 — one sweep was REJECTED as physically impossible.** The
4-point sweeps ran on a loaded host (load avg ~5, 10 users). The 2:4 fit returned slope
0.003064 µs/beat against a hard floor of 1/325 = 0.003077 — **12.9 ps/beat faster than the
clock itself, i.e. 1.0042 beats/cycle** (R² 0.99704, worst of the five). Cause: per-size
implied overhead ran 474.0 / 696.9 / 759.6 / 526.3 µs when it must be constant — a slow
4096 point and a fast 8192 point tilt the slope past the clock, and best-of-N cannot fix it
because keeping the fastest of 15 lets one lucky large sample set the slope.

Not a clock error: a genuinely faster clock would push all five largest-size points below
the 6452.8 µs floor, and none are. **The design was never in question — the soaks show
textbook linear scaling.** True 2:4 @325 brackets to ~80–81 Mrow/s.

⚠️ **NEVER compare a 325 soak number against a 300 fit number.** Different estimators, ~6%
apart (a soak carries launch overhead, a fit removes it). All cross-clock comparisons above
are soak vs soak. If the 325 marginal fits are ever wanted, re-run the five sweeps at
`--reps 25` on a quiet machine and require **beats/cycle < 1.000 AND R² ≥ 0.999 on the FIT
ROW** of each. Ignore hot differential rows — six of those exist in the sound 225/300 MHz
data and they carry no signal.

---

### S28 — §7.2 HBM vs DDR

`connectivity.cfg` only. Map several bundles onto `DDR[0]` (option (a) — no engine change)
and let Vitis insert the interconnect. GEMV is memory-bound at ~0.38 MAC/byte with no weight
reuse, so expect DDR to be dramatically slower. **That is the result**, and it is what
justifies the platform choice.

### S29 — §7.3 bus bandwidth

`PC_WIDTH` 256 → 512, plus `max_read_burst_length` / `num_read_outstanding` tuning in the
movers, plus the `stream_connect` FIFO depth. Cheapest first: the HLS pragmas need no RTL
change at all.

### ~~S30 — §7.4 core count~~ — **out of scope**

8 cores is the fixed reference configuration (see Scope). Recorded for completeness: 16-core
sparse would need **all 32 HBM PCs** and would sit exactly on the 32-interface-per-kernel
spec limit, plus a regenerated wrapper and wizard config per core count.

---

## Stage I — measurement and write-up

### S31 [S] — enable profiling

```ini
# xrt.ini
[Debug]
profile=true
timeline_trace=true
data_transfer_trace=coarse
```

These are the **OpenCL-path** keys, which is the path we chose in §0.2e — one of the
reasons for choosing it, since it is the better-supported side of the 2021.1 profiler.
`xrt.ini` also ships with the reference example, so copy that one if in doubt.

**Still verify against the XRT 2.13 docs on the server before trusting a run.** XRT here is
2.13 (branch 2022.1) while Vitis is 2021.1, and the ini schema shifted across exactly that
boundary. Silently-empty trace files are the failure mode — an empty run summary looks like
"the kernel was too fast", not like a misconfiguration. Paste me what 2.13 accepts and I
will fix this block.

### S31b [C+S] — hardware profiling monitors *(optional, only if time allows)*

**Deferred by decision 2026-08-24. RE-RAISED by the professor 2026-09-03 and DEFERRED AGAIN
— the reasoning below is unchanged and is the answer to give.** `Vitis/profile.cfg` is
written and ready if the decision is reversed; the cost is 3–5 h and a build that will very
likely not close 300 MHz. Note also that the professor's underlying question — *latency from
first HBM read to last HBM write* — **is already answered without profiling**: the OpenCL
kernel event span runs from `ap_start` to `ap_done`, so H2D, D2H and setup are already
outside it. That is the number reported in µs throughout.

`--package` options are Versal/embedded SD-card packaging -- irrelevant here. The real
controls are `--profile` at link and `xrt.ini` at run time.

**Already in place (host-side, free):** `host.cpp` uses `CL_QUEUE_PROFILING_ENABLE` and reads
`CL_PROFILING_COMMAND_START/END`, giving kernel span, H2D/D2H, throughput and a *calculated*
bandwidth (bytes known to be in the buffers / elapsed). It cannot see individual HBM channels
or the AXI-Stream links at all.

**What `--profile` would add** -- hardware monitors inserted at link:

| Option | Monitor | Gives |
|---|---|---|
| `--profile.data all:all:all` | AIM on `m_axi` | measured bytes/transactions/latency **per HBM channel** |
| same | ASM on k2k streams | per-stream transfers + **stall / starve / active cycles** |
| `--profile.exec all:all` | AM per CU | real per-CU execution time |

The ASM stall/starve data is the valuable part: it would *measure* why `beats/cycle` sits
below 1.0 instead of leaving it inferred.

**THE CATCH -- build two xclbins.** The clean build closes 300 MHz with **+0.022 ns** and the
router already reports CLB congestion. 14 AIMs + 14 ASMs + 15 AMs is real logic on the AXI
paths, in exactly the region with no slack. A profiling build may miss timing or need a lower
clock. So: report **area / Fmax / timing from the clean build**, **bandwidth / stall from the
profiling build**, and say so explicitly in the thesis -- otherwise two different designs get
quoted as one.

`Vitis/xrt.ini` is already written, with the key-name caveat in its header (XRT renamed this
group around 2020.2; this machine is Vitis 2021.1 + XRT 2.13/2022.1, straddling it). An
unrecognised key is ignored silently and a missing trace file looks exactly like "nothing was
captured".

**Do this only after** the big-stimulus + differential timing work. Monitors cannot fix a
measurement that is 99% launch overhead -- they would just measure the wrong thing precisely.

### S32 [C] — results table — ✅ **DONE 2026-09-03**

One row per configuration: throughput (rows/s), cycles/lap, bytes moved, achieved GB/s and
% of peak, LUT/FF/DSP/BRAM, achieved clock + WNS, power, and the correctness verdict.

**Delivered as `results/GEMV_avg3_results.csv` (12 rows x 34 cols)** — dense + four sparsities
+ MIXED, at 300 and 325 MHz, all under the single `run_avg3` estimator. Full detail in the
memory file `gemv-avg3-shapes-energy.md`; the numbers that go in the thesis:

| | speed-up @300 | % of theory | GFLOPS eff @325 | energy vs dense | DSP occupancy |
|---|---|---|---|---|---|
| dense | 1.00x | — | 75.30 | 1.00x | 0.959–0.967 |
| 2:4 | **1.979x** | 99.0 | 146.97 | 1.77x | 0.965–0.970 |
| 2:8 | **3.947x** | 98.7 | 293.44 | 3.51x | 0.956–0.960 |
| 2:16 | **7.833x** | 97.9 | 585.07 | 7.03x | 0.957 |
| 2:32 | **15.481x** | 96.8 | **1143.51** | **13.99x** | 0.945–0.955 |
| MIXED | 4.162x | (theory 4.267) | 310.43 | 3.80x | 0.969–0.970 |

⚠️ **PASS CRITERION FOR ANY FUTURE ROW ADDED TO THIS TABLE: it must share the estimator.**
`sweep_fit`, `power_soak` and `run_avg3` differ 5–6% for identical hardware. A row measured
another way does not belong in this table, however correct it is on its own.

### S32b [C] — matrix-shape sweep — ✅ **DONE 2026-09-03**

`Vitis/run_shapes.py` → `results/GEMV_shapes_300MHz.csv` (72 rows). 11 shapes G1–G11 x 6
configurations, **batched** (per-matrix latency = (measured − overhead) / R).

- GEOMEAN speed-ups **2.0000 / 3.9623 / 7.9318 / 15.8113** = 98.8–99.99% of theory.
- **Aspect ratio is irrelevant**: within each iso-work group (G4/G5, G6/G7/G8, G9/G10/G11)
  latencies agree to **0.32–0.82%**. Only M×N matters.
- **Pass criterion, met:** the mixed matrix must cost the mean of its four quarters. Predicted
  6716.5 µs of compute, implied 6716.5 µs — **additive to 0.66%**, and within 3% across all
  11 shapes. The prediction was registered before the measurement.

⚠️ **`data_source` must stay the LAST column.** Finished charts address this sheet by absolute
reference; inserting a column silently re-points every series with no error shown.

### S32c [C] — energy — ✅ **DONE 2026-09-03**

`make_energy_csv.py` joins the shape latencies with the 60 s soaks →
`results/GEMV_Energy_300MHz.csv` (66 rows). W × µs = µJ directly.

**Static is 84–86% of board energy in every configuration**, unmoved by shape or sparsity. The
device leaks more than the accelerator burns, so **the only way to save energy is to finish
sooner** — sparse draws MORE power (35.9 W vs 31.8 W) and still wins 13.99x at 2:32 purely on
time. Chart the **per-element** columns; absolute energy spans 864x across these shapes.

### S32d [C] — in-system utilization WITH the movers — ✅ **ALREADY EXISTED**

`sparse_reports_300slr/impl_1_kernel_util_routed.rpt` and
`dense_reports_300/impl_1_kernel_util_routed.rpt` are `report_accelerator_utilization` output
from the original links and break the routed device down **per compute unit** — all 17 sparse
CUs (`mm2s_a0/a1`, `mm2s_i0/i1/i2`, `mm2s_w0–w7`, `s2mm_c0–c3`) and 14 dense, plus the shell.
Parsed into the **System** sheet of `results/GEMV_Area_Comparison.xlsx`.

| Component | Sparse LUT | Dense LUT | Sparse cost |
|---|---|---|---|
| engine | 66,167 | 46,926 | **+41.0%** |
| `krnl_mm2s` | 20,690 | 15,365 | +34.7% |
| `krnl_s2mm` | 8,139 | 8,135 | +0.05% |
| ALL user logic | 94,996 | 70,426 | +34.9% |
| platform | 123,821 | 118,929 | +4.1% |

**+41.0% in system vs +41.1% OOC cross-validates the area result.** The movers are **30% of
the sparse accelerator** (33% dense) — quoting the engine alone understates the cost by a
third. `s2mm` identical to 4 LUTs shows the output path is untouched by sparsity.

⚠️ **A `--save-temps` rebuild adds NOTHING here.** Its only product is the routed `.dcp`.

### S34 [S] — the device floorplan picture — ⏳ **THE LAST OUTSTANDING ARTEFACT**

Vitis deletes the routed checkpoint by default, which is why no device view has ever been
possible: every existing build kept per-IP checkpoints only, never the placed-and-routed
system. `Vitis/build_floorplan.sh` (untracked) relinks both designs with `--save-temps`.

Preflight passed 2026-09-04: 172 GB on `/`, 109 GB on `/home`, `v++` present, both `.xo`
present. **Not yet confirmed launched.**

```
tmux new -s floorplan
bash ~/build_floorplan.sh 2>&1 | tee ~/floorplan_build.log
```

It builds **both** designs, one after the other — 3–5 h each. It copies the ~1 GB `.dcp` out
and deletes the 30–50 GB `_x` tree **before** starting the second, because two do not fit;
`MIN_GB=60` refuses to start a link without the headroom rather than filling a shared machine.

**It reproduces the builds that were MEASURED, and the asymmetry is deliberate:** sparse @300
**with** `slr_floorplan.cfg`, dense @300 **without**. Dense closes 300 MHz with its engine in
SLR0 beside the movers; sparse could not and needed its own die. **The two pictures showing
different placements is itself the result.** Expect: all 512 sparse DSPs in SLR1, movers and
HBM interconnect in SLR0, and 6,853 SLLs crossing SLR1↔SLR0 for the 17 AXIS streams
(`results/GEMV_Floorplan_SLR.csv`).

Then: `open_checkpoint ~/floorplan_dcp/sparse_routed.dcp`, **Window → Device**.

### S33 [C] — the write-up

The through-line to protect: **the same Python golden model validates the design from
behavioural simulation to post-implementation to hardware, unchanged.** That continuity is
worth more than any single performance number.

---

## Fast reference — the traps already known

| Trap | Where |
|---|---|
| Three Alveo cards — filter `xcl::get_xil_devices()` by BDF, never take index 0 | S16, S21 |
| `hw_emu` has **no BDF** — use index `0` there | S18 |
| Second U280 platform is poison — pass the **absolute `.xpfm`** | every `v++` |
| Vivado reuses a stale netlist on edited files — `touch` first | S3 |
| Stress-ON-only runs hide drain-latency bugs — run both | S3 |
| HLS `.xo` is target-specific; RTL `.xo` is not | S14, S19 |
| No `sw_emu` without a hand-written C model — skip it | S9 |
| `stream_connect` typos are **warnings**, not errors | S17 |
| Launch order is a non-issue — do **not** debug a hang by reordering launches | S16, S18 |
| Address replicated CUs as `"krnl_mm2s:{mm2s_w0}"` — bare name = silent wrong PC | S16 |
| Keep the OpenCL queue **out-of-order** or the movers deadlock | S16 |
| Never `setArg` a k2k stream argument | S16 |
| Shared machine — check card contention before a run | S19–S21 |
