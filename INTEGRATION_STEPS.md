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
| F | S19–S22 | **Dense on hardware, bit-exact** | 2–3 days + build time |
| G | S23–S26 | Sparse, all the way through | 3–5 days |
| H | S27–S29 | Sweeps (§7.1 architecture, §7.2 HBM/DDR, §7.3 bandwidth) | 1–2 weeks (build-bound) |
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

**Pass:** **utilisation identical** to the pre-wrapper dense row in `Utilization.xlsx` --
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
2. **the file is megabytes, not kilobytes** -- a kilobyte-sized `.xo` means the core was
   packaged as the empty wizard shell, which links happily and computes nothing;
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

### ⚠ S21b — **THE ENGINE IS SINGLE-SHOT PER PROGRAMMING** (found 2026-08-23)

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

**WORKAROUND, mandatory for every hardware run until fixed:**

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

### S22 [S] — record dense baseline

`report_utilization`, `report_timing_summary` (post-route, from the `_x` build directory),
kernel wall-clock from XRT, and `xbutil examine` power. **This is where the clock decision
gets made** — if 300 MHz closes easily, rebuild higher and find where dense stops closing.
Dense meeting a higher clock than sparse is itself a thesis result.

---

## Stage G — sparse

### S23 [C+S] — repeat S1–S21 for `two2N`

Everything is the same shape with three deltas:

1. **17 streams, not 14** — `two2N_axis.vhd` adds `s_axis_ind0..2`, and per §0.2d the
   wrapper must **synthesise a TLAST for the index streams**, which have no `tlast` port
   today. Mirror the weight TLAST.
2. **Three more `krnl_mm2s` CUs** (`nk=krnl_mm2s:13:...`) for the index PCs — no new kernel
   source. The **2-bit sparsity code sits in index PC2 padding, bits [641:640]**, written
   into the buffer by the Python packer; no mover knows about it.
3. `sp=` runs to 17 PCs; `stream_connect` to 17 lines.

### S24 [S] — hw_emu, then hardware, one sparsity mode (2:16 — smallest stimulus)

### S25 [S] — the full sparsity ladder on hardware: 2:4, 2:8, 2:16, 2:32

### S26 [S] — the mixed runtime-reconfiguration case

**Pass:** sparsity changing between windows without a reload, bit-exact throughout. This is
the architecture's headline claim and it deserves its own hardware evidence.

*(The §1.2 core-count re-verification that used to sit here is out of scope — see Scope.)*

---

## Stage H — the sweeps (one variable per `.xclbin`)

### S27 — §7.1 architecture: dense vs sparse × 4 modes

Already collected by S22 + S25. Also compute **bytes moved** — sparse carries indices
(2048 + 640 b vs dense's 2048, a 31 % per-beat overhead), so net data moved is
`(2/M) × 21/16` → 66 / 33 / 16 / 8 %. The 2:4 asymmetry (2× faster, only 34 % less data)
is the most interesting single number in the thesis.

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

### S32 [C] — results table

One row per configuration: throughput (rows/s), cycles/lap, bytes moved, achieved GB/s and
% of peak, LUT/FF/DSP/BRAM, achieved clock + WNS, power, and the correctness verdict.

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
