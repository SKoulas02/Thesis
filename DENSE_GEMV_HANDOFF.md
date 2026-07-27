# Handoff Brief — Build a Dense GEMV Variant as a Comparison Baseline

## Your role

I'm building an FPGA thesis project. A **2:M sparse GEMV accelerator is complete and fully
verified**. I now need a **dense GEMV variant** built as the comparison baseline for the thesis
evaluation. You are helping me design, write, and debug the VHDL for that dense variant.

## Hard constraints on how we work

- **You must NOT run Vivado or Vitis.** I run all synthesis, implementation, and simulation
  myself. You write/edit files and tell me what to run.
- **You must NOT push, pull, or rebase git.** You may read files, edit files, and make local
  commits. I handle all remote git operations manually.
- You may run Python (the golden-model / compare scripts).
- Never commit `.xsim/`, `vivado*.log`, or `.Xil/` directories.

## Environment

- Target: **Xilinx Alveo U280**, Vivado/Vitis **2022.2**
- Languages: **VHDL** (hardware), **Python** (golden model / verification)
- Number format: **bfloat16** (1 sign / 8 exp / 7 mantissa), 16 bits per element
- Xilinx Floating-Point IP for multiply / add / accumulate

---

# PART 1 — What already exists (the sparse design)

## Concept

A runtime-reconfigurable **2:M sparse GEMV** engine. "2:M" means every group of M weights
contains exactly 2 non-zeros. It supports **2:4, 2:8, 2:16, 2:32**, selected at runtime by a
2-bit `Sparsity` code (`00`/`01`/`10`/`11`).

## Core design principle

The multiplier count is fixed. What changes with sparsity is **how many cycles each block
accumulates its row**. There is **no reduction tree** — every C Block owns one output row and
accumulates it itself. Blocks are never summed together.

## Hierarchy

```
Top Module (two2N) — pure AXI4-Stream dataflow
├── Weights FIFO  (8 weight PCs = 2048 b, 3 index PCs = 640 b used) → barrier join
├── Activation ingress FIFO (2 PCs = 512 b) → barrier join
│      └── Replay / recirculation buffer (holds the vector, replays it per row-pass)
├── 8 × C Core
│      └── 8 × C Block  (each owns 1 output row)
│             └── 2 × multiplier + 1 adder + 1 accumulator
└── Output FIFO (fork → 4 output PCs = 1024 b)
```

64 blocks total → **64 output rows per flush**. All buses are built from 256-bit HBM
pseudo-channel (PC) beats. All widths are **VHDL generics** (`PC_WIDTH`, `W_PCS`, `IND_PCS`,
`A_PCS`, `C_PCS`, `CORES_NUM`, `BLOCKS_NUM`), so the structure is parameterizable.

## The C Block (innermost unit)

1. 2 indices drive a **mux that gathers 2 activations out of the 32-element window**
   (this is the sparsity gather).
2. 2 multipliers: each gathered activation × its weight.
3. 1 adder: sums the two products.
4. 1 accumulator: sums every valid beat, **flushes on `tlast_in`** → block output.

## The freeze mechanism (`sparsity_lock`)

A 32-element activation window is held (frozen) while each block walks its row's non-zeros two
at a time:

| Sparsity | non-zeros/row per window | freeze cycles = `32/M` |
|----------|--------------------------|------------------------|
| 2:4      | 16                       | 8                      |
| 2:8      | 8                        | 4                      |
| 2:16     | 4                        | 2                      |
| 2:32     | 2                        | 1 (no freeze)          |

The freeze counter lives in the **top module**; the C Cores are sparsity-agnostic.

## tlast — two distinct markers

- **Activation tlast** = end-of-vector → drives the **accumulator flush** (once per replay lap).
- **Weight tlast** = end of the weight matrix → the **end-of-calculation** marker, aligned with
  the drained output to set the final output tlast.

## Verification status: COMPLETE

The sparse design passes a **bit-exact** co-simulation against a Python golden model on a
1024-element vector × 1024-row matrix in 4 sparsity groups (256 rows each of 2:4/2:8/2:16/2:32,
16 replay laps, 1920 weight beats) — **all 1024 rows at 0.000% error**, while simultaneously
under stress: random input-valid gaps on the weight/index streams, random gaps on the
activation stream, and 30% random output back-pressure. Synthesis timing is met.

---

# PART 2 — Verification infrastructure (REUSE THIS)

This is the most valuable asset in the project. Three pieces:

1. **`gemv4_cosim_gen.py`** — Python golden model. Generates packed bus-beat files
   (`weights.hex`, `indices.hex`, `activations.hex`) plus `golden.txt` (expected output rows).
   It models the datapath's exact rounding: **multiply and add use round-to-nearest-even; the
   accumulator is exact internally (fixed-point) and truncates to bf16 only at flush.** This
   rounding split is validated bit-exact at scale — do not change it.

2. **`two2N_TB.vhd`** — file-driven VHDL testbench. Reads the hex files with
   `std_logic_textio` `hread`, drives beats gated on `tready`, and dumps every real output
   transfer to `output.txt` (64 lanes per beat). It has **stress knobs**:
   `WGT_GAP_MAX`, `ACT_GAP_MAX` (random input bubbles via `ieee.math_real.uniform`) and
   `TREADY_STALL` (random output back-pressure). Completion is detected by a quiet period, so
   it also catches hangs. **Uses absolute file paths** (XSim's CWD is the sim run directory).

3. **`compare_gemv4.py`** — diffs `golden.txt` against `output.txt` and writes a report with
   hex, decimal, and relative error per row. Expect bit-identical.

**Bus packing conventions (little-endian everywhere — element 0 at the LSB end):**

- **Weights**, 2048 b: core `i` = `[256i+255 : 256i]`; within a core, block `b` =
  `[32b+31 : 32b]`; `w0 = [15:0]`, `w1 = [31:16]`.
- **Indices**, 768 b: core `i` = `[80i+79 : 80i]`; block `b` = `[10b+9 : 10b]`;
  `idx0 = [4:0]`, `idx1 = [9:5]` (5-bit indices). **Sparsity rides at `[641:640]`.**
- **Activations**, 512 b: element `k` = `[16k+15 : 16k]`.
- **Output**, 1024 b: lane `r = 8*core + block` at `[16r+15 : 16r]`.

---

# PART 3 — YOUR TASK: the dense variant

## The key insight — dense is the same skeleton with two things removed

Dense GEMV means every activation in the window is used by every row. Working it out:

- A block does 2 MACs/cycle. A 32-element window therefore takes **16 cycles** per row.
- So **dense = the existing architecture with the freeze counter fixed at 16 and the index
  gather removed.**

This makes the comparison genuinely apples-to-apples (identical FIFOs, identical accumulator,
identical output path) and isolates exactly what sparsity costs and buys.

## What changes

| Item | Sparse | Dense |
|---|---|---|
| Freeze / accumulate cycles per window | `32/M` (8/4/2/1) | **16, fixed** |
| Index stream | 3 PCs, 640 b/beat, 5-bit indices | **removed entirely** |
| C Block front end | 2 × 32:1 gather mux per block (64 blocks → 128 muxes) | **one shared 16:1 element-pair select**, broadcast to all blocks |
| `Sparsity` code + runtime reconfig | yes | **removed** |
| Weight bus | 2048 b (128 weights/cycle) | 2048 b — **unchanged** |
| Activation bus | 512 b (32-el window) | 512 b — **unchanged** |
| Output | 1024 b, 64 rows/flush | **unchanged** |
| HBM PCs used | 17 | **14** (no index PCs) |

**The big area saving to measure:** in dense, at cycle `c` (0..15) *every* block uses window
elements `2c` and `2c+1` — the same pair for all 64 blocks, because they're all at the same
position in their respective rows. So the 128 independent 32:1 muxes collapse into a single
shared select (or simply a shift register that shifts the window right by 32 bits each cycle
and takes the low 32 bits). Quantifying that LUT difference is a core thesis result.

**What stays identical:** the activation replay/recirculation buffer, all FIFOs and their
barrier-join/fork structure, the accumulator flush-on-activation-tlast semantics, the
end-of-calc weight-tlast alignment, and the global gate.

## Expected results to reproduce (use these as correctness checks)

For a vector of `V` elements and a lap producing 64 rows:

- Weight beats per lap: **dense = `V/2`**, sparse 2:M = `V/M`.
- Therefore **sparse speedup over dense = `M/2`** → 2× (2:4), 4× (2:8), 8× (2:16), 16× (2:32).
- Concrete check with `V = 1024` (32 windows): dense = **512 beats/lap**; 2:4 = 256, 2:8 = 128,
  2:16 = 64, 2:32 = 32.

**Data movement (the more interesting result).** Sparse beats are fatter because of indices:
2048 b weights + 640 b indices vs dense's 2048 b — a 31% per-beat overhead. Net bytes moved by
sparse relative to dense = `(2/M) × 21/16`:

| Sparsity | data moved vs dense | speedup vs dense |
|---|---|---|
| 2:4  | 66% (only 34% saved) | 2× |
| 2:8  | 33% | 4× |
| 2:16 | 16% | 8× |
| 2:32 | 8% | 16× |

Note the asymmetry at 2:4 — 2× faster but only 34% less data — because index overhead eats the
bandwidth win at low sparsity. That's worth calling out in the thesis.

**Why bandwidth dominates:** GEMV has **no weight reuse** (each weight feeds exactly one MAC),
giving an arithmetic intensity of ~0.38 MAC/byte. Both designs are memory-bound by
construction, which is why the platform targets HBM.

## Suggested order of work

1. Adapt `gemv4_cosim_gen.py` into a dense generator: drop the index file, emit weights in
   positional order, fix the cadence at 16 cycles/window, keep the identical bf16 rounding model.
2. Write the dense C Block (drop the gather mux), then the dense C Core.
3. Write the dense top module: reuse the FIFO wrappers minus the index path; the control
   simplifies a lot (fixed freeze of 16, no `Sparsity` sampling, no runtime reconfiguration).
4. Adapt the testbench (fewer input streams; keep all stress knobs) and run the same
   verification ladder, ending with the 1024×1024 stress case.
5. Synthesize and collect the comparison numbers: **LUT / FF / DSP / BRAM, Fmax, cycles per
   lap, bytes moved**.

---

# PART 4 — Hard-won lessons (do NOT rediscover these)

These cost significant debugging time on the sparse design. They will bite the dense one too.

1. **Xilinx Floating-Point IP holds its output valid.** In the IP configuration you must tick
   **"RESULT channel has TREADY"** (Blocking mode) on the multiplier, adder, *and* accumulator.
   Without it, `m_axis_result_tvalid` sticks high and you get repeated/extra output beats.

2. **Gate the accumulator flush properly.** The block's output must be gated on
   `accum_valid AND accum_tlast`, otherwise you emit two output beats per dot product instead
   of one.

3. **FWFT FIFO drain hazard.** Every FIFO in this design is First-Word-Fall-Through. A join's
   `all_valid` stays high *through* the cycle its last beat is popped. If your control samples
   `ready` at one edge but consumes the beat a cycle later (registered `rd_en`/`valid`), an
   input bubble makes it re-consume a stale head and corrupt everything. **Fix: put a 1-deep
   register stage between each ingress FIFO and the consumer**, with
   `fill = head_ready AND (NOT stage_valid OR rd_en)` and
   `ready = head_ready OR (stage_valid AND NOT rd_en)`. That exported `ready` equals next-cycle
   `stage_valid` exactly, so every commit is guaranteed a real beat and a drained cycle becomes
   a clean stall. Full throughput is preserved. **Build this in from the start.**

4. **Co-time metadata with its data.** Anything the control reads *about* the beat being
   consumed (tlast, mode bits) must be sampled on the same cycle as the data. A registered mux
   on the activation window lagged one cycle and silently mispaired windows. Distinguish
   *decision-time* metadata (about the beat you'll consume next) from *consumption-time*
   metadata (about the beat you're consuming now).

5. **Vivado skips recompiling files whose mtime looks stale.** After editing VHDL, `touch` the
   file, or the simulation will silently run the old netlist. Use **Relaunch**, not Restart.

6. **Keep little-endian packing consistent** across the Python model, the testbench, and the
   RTL slicing. Most "wrong results" turn out to be a packing mismatch, not a datapath bug.

7. **Verify resource/timing claims by reading the actual reports.** Don't assert area or
   bandwidth tradeoffs from intuition — the numbers are frequently counterintuitive.

---

# PART 5 — How to start

Please begin by asking me for whatever you need from the existing project (file listings, the
sparse `c_block` / `c_core` / top module, or the Python generator), then propose the dense C
Block design. Build it as a **minimal diff from the sparse design** so the comparison isolates
the cost of sparsity support — don't redesign from scratch.
