# Dense GEMV — comparison baseline for the 2:M sparse engine

Minimal diff from the verified sparse design (`GEMV_4.0_Source`). Everything that is
not *sparsity support* is carried over unchanged, so the sparse-vs-dense delta
isolates exactly what sparsity costs and buys.

## What is new vs. what is reused

**New files (this tree):**

| File | Sparse counterpart | Change |
|---|---|---|
| `Design/c_block_dense.vhd` | `c_block_4.0.vhd` | index gather removed; `A_in` 512 b → **32 b** (the pre-selected pair). Multipliers/adder/accumulator/tlast gate byte-identical. |
| `Design/c_core_dense.vhd` | `c_core_4.0.vhd` | frozen window + 16 gather muxes → **512-bit shift register**, low 32 bits shared by all 8 blocks. `Indices` port removed, `sparsity_lock` → `win_lock`. |
| `Design/weights_fifo_dense.vhd` | `weights_fifo_4.0_2k.vhd` | 3 index PCs and the 2-bit sparsity code removed → 8-PC join, 2048 b/beat. |
| `Design/top_module_dense.vhd` | `top_module_2N.vhd` | freeze fixed at 16; `Sparsity`/`sparsity_next`/`spar_stage`, the whole `flush_2to32` 2:32 path, and the index ports removed. Ingress stages, replay buffer, gate, tlast discipline unchanged. |
| `Simulation/dense_TB.vhd` | `two2N_TB.vhd` | index streams dropped; all stress knobs identical. |
| `Emulation/gemv_dense_gen.py` | `gemv4_cosim_gen.py` | no index file, cadence fixed at 16 beats/window, **identical bf16 rounding model**. |
| `Emulation/compare_dense.py` | `compare_gemv4.py` | same, writes `compare_dense_report.txt`. |

**Reused verbatim — byte-identical copies of the sparse originals, already present in
`Design/`:**

```
multiplier_wrapper_4.0.vhd   adder_wrapper_4.0.vhd   accumulator_wrapper_4.0.vhd
vector_fifo_4.0.vhd          vector_fifo_cycle_4.0.vhd          c_fifo_4.0.vhd
```

These are **copies, not references** — an edit to the `GEMV_4.0_Source` original does
not propagate here (and vice versa). If either side is ever touched, re-sync the pair
before collecting comparison numbers, or the sparse-vs-dense delta stops isolating
sparsity. Quick check:

```sh
for f in multiplier_wrapper_4.0.vhd adder_wrapper_4.0.vhd accumulator_wrapper_4.0.vhd \
         vector_fifo_4.0.vhd vector_fifo_cycle_4.0.vhd c_fifo_4.0.vhd; do
  diff -q "../GEMV_4.0_Source/Design/$f" "Design/$f"
done
```

**IP needed (same configs as the sparse project — do not re-tune, it would perturb
the comparison):** the three bf16 Floating-Point IPs (multiplier / adder /
accumulator, all with **RESULT channel has TREADY** ticked), `axis_data_fifo_pc`,
`axis_data_fifo_v`, `axis_data_fifo_c`, `fifo_gen_vector_cycle`. All FWFT.

`axis_data_fifo_pc` is still needed (the weight PCs use it); only the *index*
instances of it disappear.

## Cadence

Each block does 2 MACs/cycle, so one 32-element window lasts **16 beats** — that is
`FREEZE_BEATS = A_IDX/2` in `top_module_dense.vhd`, replacing the runtime `32/M`
counter. Per lap: 64 output rows, `V/2` weight beats for a `V`-element vector.

| | beats/lap (V=1024) | vs dense |
|---|---|---|
| dense | 512 | 1× |
| 2:4 | 256 | 2× |
| 2:8 | 128 | 4× |
| 2:16 | 64 | 8× |
| 2:32 | 32 | 16× |

## Verification ladder

Same flow as the sparse design. From `Emulation/`:

```
python gemv_dense_gen.py --nwin 1  --nlaps 1              # 1. baseline, 16 beats
python gemv_dense_gen.py --nwin 2  --nlaps 1              # 2. multi-window (shift reg advances)
python gemv_dense_gen.py --nwin 1  --nlaps 2              # 3. replay buffer / lap boundary
python gemv_dense_gen.py --nwin 2  --nlaps 2              # 4. both
python gemv_dense_gen.py --nwin 2  --nlaps 2 --value-hi 100   # 5. bf16 rounding model
python gemv_dense_gen.py --nwin 32 --nlaps 16             # 6. full 1024 x 1024 stress
```

Then, for each rung: run `dense_TB` in XSim (top = `dense_TB`), and

```
python compare_dense.py
```

Expect `PASS (bit-exact)`, `0 mismatch(es)`. Rung 6 = 8192 weight beats, 16 laps,
1024 rows, under the full stress knobs (`WGT_GAP_MAX=4`, `ACT_GAP_MAX=4`,
`TREADY_STALL=0.30`).

Reminder: **touch the edited `.vhd` before relaunching**, and use *Relaunch*, not
*Restart* — Vivado skips recompiling files whose mtime looks stale.

## Known open item

`settle` (the one-cycle lap-boundary bubble) is retained for behavioural parity with
the verified sparse control, but its original reason — resampling the next group's
`Sparsity` from a refilled stage — does not exist in dense. It costs 1 cycle per lap
(16 of 8192 on the full case, 0.2%). Once rung 3/4 pass it can be deleted and
re-verified as a micro-optimisation.

## Numbers to collect after synthesis

LUT / FF / DSP / BRAM, Fmax, cycles per lap, bytes moved — against the same figures
from the sparse project. Read them out of the actual utilization/timing reports.
