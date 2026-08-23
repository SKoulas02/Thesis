"""Beat-level co-simulation generator + golden model for the DENSE GEMV top module
(dense_gemv) -- the comparison baseline for the 2:M sparse engine (two2N).

Adapted from gemv4_cosim_gen.py. Two things are removed, nothing else changes:

  * NO indices file. Dense weights are positional: at beat c of a window (c=0..15)
    EVERY block uses activation elements 2c and 2c+1, because all 64 blocks sit at
    the same position in their respective rows.
  * NO sparsity code. The freeze is a constant 16 beats per 32-element window
    (2 MACs per block per cycle), so there is nothing to reconfigure at runtime.

The bf16 rounding model is IDENTICAL to the sparse generator and must stay that
way -- it is what makes the sparse-vs-dense numerical comparison meaningful:
  multiply and add use round-to-nearest-even; the accumulator is exact internally
  and truncates to bf16 only at the flush.

Number format : bfloat16 (1 sign | 8 exp, bias 127 | 7 mantissa), 4 hex chars each.

Datapath modelled (per c_block_dense -> c_core_dense -> dense_gemv):
  Per block, per beat c of window w:
      p0 = bf16_rne(w0 * A[w][2c]) ; p1 = bf16_rne(w1 * A[w][2c+1])
      add = bf16_rne(p0 + p1)
  Accumulator: sums 'add' over every beat of ONE lap (internal ~exact) and emits
  bf16 by TRUNCATION at the flush (activation tlast).

Verification ladder:
  1. --nwin 1  --nlaps 1        baseline (one window, 16 beats)
  2. --nwin 2  --nlaps 1        multi-window (window shift register advances)
  3. --nwin 1  --nlaps 2        replay buffer / lap boundary
  4. --nwin 2  --nlaps 2        both
  5. --value-hi 100             bf16 rounding model
  6. --nwin 32 --nlaps 16       full 1024-element vector x 1024 rows (8192 beats)

Model of the hardware run (one activation vector, reused across laps):
  * activation vector = NWIN windows of 32 elements, loaded ONCE (lap 0 load
    phase), then recirculated from the replay buffer for the remaining laps.
  * each lap uses its OWN weights and produces one 64-lane output beat (flush).
    Beats per lap = NWIN * 16 = V/2 for a V-element vector.
  * weight tlast (end-of-matrix) rides the very last weight beat; activation
    tlast marks each vector-end (once per lap).

Bus packing (little-endian; matches dense_gemv/c_core_dense slicing -- IDENTICAL
to the sparse design for weights, activations and output):
  weights  2048b : core i = bits[256i+255:256i]; block b = [32b+31:32b];
                   w0=[15:0], w1=[31:16]  -> flat elem (16i+2b)=w0, (16i+2b+1)=w1
  activ.    512b : element k = [16k+15:16k]  (k = 0..31, one 32-el window)
  output   1024b : lane r = 8*core + block at [16r+15:16r]

Output file order: lap 0 rows 0..63, lap 1 rows 0..63, ...  (matches dense_TB
dump order).
"""

from pathlib import Path
import argparse
import random
import struct

HERE = Path(__file__).parent

# ---- Defaults (overridable on the command line) ----------------------------
NWIN       = 1           # 32-element activation windows in the vector
NLAPS      = 1           # weight-row groups reusing the same vector (>=2 tests replay)
SEED       = 20250701
VALUE_LO   = 1           # exact-in-bf16 integer range for weights & activations
VALUE_HI   = 7           #   products <= 49, small sums -> exact in bf16 (no rounding)

CORES      = 8
BLOCKS     = 8
WIN_ELEMS  = 32          # activation elements per window (512b / 16)
BEATS_WIN  = WIN_ELEMS // 2   # 16 -- dense freeze: 2 elements per block per cycle

WFILE = HERE / "weights.hex"
AFILE = HERE / "activations.hex"
GFILE = HERE / "golden.txt"


# ---- bfloat16 helpers (identical to the sparse generator) -------------------
# PURE PYTHON -- no torch. The university server has no PyTorch and installing it
# for three lines of bit twiddling is not worth it. Verified BIT-IDENTICAL to the
# previous torch implementation by regenerating the checked-in stimulus
# (--nwin 2 --nlaps 2, default seed) and diffing weights.hex / activations.hex /
# golden.txt byte for byte. Do not "simplify" the rounding below -- it is what
# makes the golden model match the Floating-Point IP.
#
# Values here are bf16 quantities carried in Python floats (i.e. doubles that
# happen to hold exactly representable bf16 values), so .float() is a no-op and
# the arithmetic is exact until the explicit rounding calls.

def _fp32_bits(x):
    return struct.unpack(">I", struct.pack(">f", float(x)))[0]

def _bits_fp32(b):
    return struct.unpack(">f", struct.pack(">I", b & 0xFFFFFFFF))[0]

def _bf16_rne_raw(x):
    """fp32 -> bf16 raw bits, round-to-nearest-EVEN (what the FP IPs do)."""
    b = _fp32_bits(x)
    lsb = (b >> 16) & 1                    # even/odd of the surviving mantissa
    b = (b + 0x7FFF + lsb) & 0xFFFFFFFF    # tie goes to even
    return (b >> 16) & 0xFFFF

def f_to_bf16(x):
    return _bits_fp32(_bf16_rne_raw(x) << 16)

def bf16_raw(t) -> int:
    return _bf16_rne_raw(t)

def bf16_rne(x):
    """Round-to-Nearest-Even bf16 (Multiplier / Adder IPs)."""
    return _bits_fp32(_bf16_rne_raw(x) << 16)

def double_to_bf16_trunc_raw(s: float) -> int:
    """fp32 -> bf16 by truncation (Accumulator IP output); returns 16-bit hex value."""
    bits = struct.unpack(">I", struct.pack(">f", float(s)))[0]
    return (bits >> 16) & 0xFFFF


def main() -> None:
    ap = argparse.ArgumentParser(description="Dense GEMV beat-level co-sim generator")
    ap.add_argument("--nwin",  type=int, default=NWIN)
    ap.add_argument("--nlaps", type=int, default=NLAPS)
    ap.add_argument("--seed",  type=int, default=SEED)
    ap.add_argument("--value-lo", type=int, default=VALUE_LO)
    ap.add_argument("--value-hi", type=int, default=VALUE_HI)
    a = ap.parse_args()

    nlaps = a.nlaps

    # beat metadata: for each global weight beat -> (lap, window, beat-in-window).
    # Dense cadence is fixed: BEATS_WIN beats per window, every window, every lap.
    beat_meta = []
    for L in range(nlaps):
        for win in range(a.nwin):
            for c in range(BEATS_WIN):
                beat_meta.append((L, win, c))
    NBEATS = len(beat_meta)

    rng = random.Random(a.seed)

    # activations: NWIN windows of 32 values (loaded once, reused across laps)
    act_val = [[f_to_bf16(rng.randint(a.value_lo, a.value_hi)) for _ in range(WIN_ELEMS)]
               for _ in range(a.nwin)]
    act_raw = [[bf16_raw(act_val[w][k]) for k in range(WIN_ELEMS)] for w in range(a.nwin)]

    # weights per beat, per (core, block): (w0, w1). No indices -- the element pair
    # is implied by the beat's position in its window.
    w_val = [[[ (f_to_bf16(rng.randint(a.value_lo, a.value_hi)),
                 f_to_bf16(rng.randint(a.value_lo, a.value_hi)))
               for _ in range(BLOCKS)] for _ in range(CORES)] for _ in range(NBEATS)]

    # ---- golden: per-lap accumulate (exact internal), bf16-trunc at flush --
    golden = []                              # nlaps * 64 rows, in emit order
    for L in range(nlaps):
        lap_beats = [b for b in range(NBEATS) if beat_meta[b][0] == L]
        lap_out = [0] * (CORES * BLOCKS)
        for i in range(CORES):
            for b in range(BLOCKS):
                acc = 0.0
                for gbeat in lap_beats:
                    _L, win, c = beat_meta[gbeat]
                    w0, w1 = w_val[gbeat][i][b]
                    a0 = act_val[win][2 * c]         # dense: positional pair,
                    a1 = act_val[win][2 * c + 1]     # the same for every block
                    p0 = bf16_rne(w0 * a0)
                    p1 = bf16_rne(w1 * a1)
                    add = bf16_rne(p0 + p1)
                    acc += float(add)
                lap_out[8 * i + b] = double_to_bf16_trunc_raw(acc)
        golden.extend(lap_out)

    # ---- pack + write bus beats -------------------------------------------
    with WFILE.open("w") as wf:
        for beat in range(NBEATS):
            wbus = 0
            for i in range(CORES):
                for b in range(BLOCKS):
                    w0, w1 = w_val[beat][i][b]
                    e0 = 16 * i + 2 * b
                    wbus |= bf16_raw(w0) << (16 * e0)
                    wbus |= bf16_raw(w1) << (16 * e0 + 16)
            wf.write(f"{wbus:0512X}\n")              # 2048 bits

    with AFILE.open("w") as af:
        for w in range(a.nwin):
            abus = 0
            for k in range(WIN_ELEMS):
                abus |= act_raw[w][k] << (16 * k)
            af.write(f"{abus:0128X}\n")              # 512 bits

    with GFILE.open("w") as gf:
        for raw in golden:
            gf.write(f"{raw:04X}\n")                 # one bf16 per line

    velems = a.nwin * WIN_ELEMS
    print(f"DENSE  V={velems} elements  NWIN={a.nwin}  laps={nlaps}  "
          f"beats/lap={a.nwin*BEATS_WIN} (=V/2)  total beats={NBEATS}")
    print(f"  weights.hex    : {NBEATS} beats x 2048b -> {WFILE.name}")
    print(f"  activations.hex: {a.nwin} windows x 512b  -> {AFILE.name}")
    print(f"  golden.txt     : {len(golden)} rows ({nlaps} lap(s) x 64) -> {GFILE.name}")
    print(f"  (no indices.hex -- dense removes the 3 index PCs: 17 HBM PCs -> 14)")


if __name__ == "__main__":
    main()
