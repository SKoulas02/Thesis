"""Beat-level co-simulation generator + golden model for the GEMV 4.0 top module (two2N).

Generates the *packed bus beats* that two2N consumes directly (little-endian, exactly
the slicing two2N/c_core perform), writes them as hex files the file-driven testbench
(two2N_TB.vhd) reads, and computes the bit-faithful golden output the hardware should
produce.  Run compare_gemv4.py to diff golden.txt vs output.txt.

Number format : bfloat16 (1 sign | 8 exp, bias 127 | 7 mantissa), 4 hex chars each.

Datapath modelled (per c_block_4.0 -> c_core_4.0 -> two2N):
  Per block, per beat:  p0 = bf16_rne(w0 * A[idx0]) ; p1 = bf16_rne(w1 * A[idx1])
                        add = bf16_rne(p0 + p1)
  Accumulator: sums 'add' over every beat of ONE lap (internal ~exact) and emits bf16 by
  TRUNCATION at the flush (activation tlast). Validated bit-exact vs the Xilinx FP IPs
  (mult/add RNE, accumulator trunc) with --value-hi 100.

RUNTIME SPARSITY: the sparsity is NOT a separate signal -- it rides in each index beat at
bits [641:640]. two2N re-samples it at every window-load and sets the freeze counter. So a
runtime-reconfigurable run = laps whose index beats carry DIFFERENT sparsity codes. Use
--sparsities to give one code per lap (e.g. --sparsities 00,10,11 runs 3 laps at 2:4, 2:16,
2:32 over the same replayed vector). Each lap flushes 64 rows at its own sparsity.

Verification ladder:
  1. --sparsity 10 --nwin 1 --nlaps 1        baseline
  2. --sparsity 10 --nwin 2                   multi-window
  3. --sparsity 10 --nlaps 2                  replay buffer
  4. --sparsity 00|01|11                      sparsity sweep
  5. --value-hi 100                           bf16 rounding model
  6. --sparsities 00,01,10,11                 RUNTIME sparsity reconfiguration

Model of the hardware run (one activation vector, reused across laps):
  * activation vector = NWIN windows of 32 elements, loaded ONCE (lap 0 load phase),
    then recirculated from the replay buffer for the remaining laps.
  * each lap uses its OWN weights/indices AND its OWN sparsity, and produces one 64-lane
    output beat (flush). Lap L beats/window = 32/M(sparsity[L]).
  * weight tlast (end-of-matrix) rides the very last weight beat; activation tlast marks
    each vector-end (once per lap).

Bus packing (little-endian; matches two2N/c_core slicing):
  weights  2048b : core i = bits[256i+255:256i]; block b = [32b+31:32b];
                   w0=[15:0], w1=[31:16]  -> flat elem (16i+2b)=w0, (16i+2b+1)=w1
  indices   768b : [639:0] used; core i = [80i+79:80i]; block b = [10b+9:10b];
                   idx0=[4:0], idx1=[9:5] (5-bit). Sparsity at [641:640].
  activ.    512b : element k = [16k+15:16k]  (k = 0..31, one 32-el window)
  output   1024b : lane r = 8*core + block at [16r+15:16r]

Output file order: lap 0 rows 0..63, lap 1 rows 0..63, ...  (matches two2N_TB dump order).
"""

from pathlib import Path
import argparse
import random
import struct
import torch

HERE = Path(__file__).parent

# ---- Defaults (overridable on the command line) ----------------------------
SPARSITY   = "10"        # 00=2:4, 01=2:8, 10=2:16, 11=2:32
NWIN       = 1           # 32-element activation windows in the vector
NLAPS      = 1           # weight-row groups reusing the same vector (>=2 tests replay)
SEED       = 20250701
VALUE_LO   = 1           # exact-in-bf16 integer range for weights & activations
VALUE_HI   = 7           #   products <= 49, small sums -> exact in bf16 (no rounding)

CORES      = 8
BLOCKS     = 8
WIN_ELEMS  = 32          # activation elements per window (512b / 16)

WFILE = HERE / "weights.hex"
IFILE = HERE / "indices.hex"
AFILE = HERE / "activations.hex"
GFILE = HERE / "golden.txt"

SP_MAP = {"00": 4, "01": 8, "10": 16, "11": 32}


# ---- bfloat16 helpers (torch, matching the 2:4 emulation style) ------------
def f_to_bf16(x) -> torch.Tensor:
    return torch.tensor(float(x), dtype=torch.float32).bfloat16()

def bf16_raw(t: torch.Tensor) -> int:
    return int(t.view(torch.int16).item()) & 0xFFFF

def bf16_rne(x: torch.Tensor) -> torch.Tensor:
    """Round-to-Nearest-Even bf16 (Multiplier / Adder IPs)."""
    return x.float().bfloat16()

def double_to_bf16_trunc_raw(s: float) -> int:
    """fp32 -> bf16 by truncation (Accumulator IP output); returns 16-bit hex value."""
    bits = struct.unpack(">I", struct.pack(">f", float(s)))[0]
    return (bits >> 16) & 0xFFFF


def main() -> None:
    ap = argparse.ArgumentParser(description="GEMV 4.0 beat-level co-sim generator")
    ap.add_argument("--sparsity", default=SPARSITY, choices=list(SP_MAP))
    ap.add_argument("--sparsities", default=None,
                    help="comma-separated per-lap sparsity codes (overrides --sparsity/--nlaps), "
                         "e.g. 00,10,11 -> 3 laps at 2:4,2:16,2:32 over the same replayed vector")
    ap.add_argument("--nwin",  type=int, default=NWIN)
    ap.add_argument("--nlaps", type=int, default=NLAPS)
    ap.add_argument("--seed",  type=int, default=SEED)
    ap.add_argument("--value-lo", type=int, default=VALUE_LO)
    ap.add_argument("--value-hi", type=int, default=VALUE_HI)
    a = ap.parse_args()

    # per-lap sparsity list (one code per lap)
    if a.sparsities:
        lap_sp = [s.strip() for s in a.sparsities.split(",") if s.strip()]
        bad = [s for s in lap_sp if s not in SP_MAP]
        if bad:
            ap.error(f"invalid sparsity code(s) {bad}; choose from {list(SP_MAP)}")
    else:
        lap_sp = [a.sparsity] * a.nlaps
    nlaps   = len(lap_sp)
    lap_cyc = [32 // SP_MAP[s] for s in lap_sp]     # accumulate beats per window, per lap

    # beat metadata: for each global weight/index beat -> (lap, sparsity_code, window)
    beat_meta = []
    for L in range(nlaps):
        for win in range(a.nwin):
            for _c in range(lap_cyc[L]):
                beat_meta.append((L, lap_sp[L], win))
    NBEATS = len(beat_meta)

    rng = random.Random(a.seed)

    # activations: NWIN windows of 32 values (loaded once, reused across laps)
    act_val = [[f_to_bf16(rng.randint(a.value_lo, a.value_hi)) for _ in range(WIN_ELEMS)]
               for _ in range(a.nwin)]
    act_raw = [[bf16_raw(act_val[w][k]) for k in range(WIN_ELEMS)] for w in range(a.nwin)]

    # weights & indices per beat, per (core, block): (w0,w1) tensors + (i0,i1) 0..31
    w_val = [[[ (f_to_bf16(rng.randint(a.value_lo, a.value_hi)),
                 f_to_bf16(rng.randint(a.value_lo, a.value_hi)))
               for _ in range(BLOCKS)] for _ in range(CORES)] for _ in range(NBEATS)]
    idx   = [[[ (rng.randint(0, WIN_ELEMS-1), rng.randint(0, WIN_ELEMS-1))
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
                    win = beat_meta[gbeat][2]
                    w0, w1 = w_val[gbeat][i][b]
                    i0, i1 = idx[gbeat][i][b]
                    p0 = bf16_rne(w0 * act_val[win][i0].float())
                    p1 = bf16_rne(w1 * act_val[win][i1].float())
                    add = bf16_rne(p0.float() + p1.float())
                    acc += float(add)
                lap_out[8 * i + b] = double_to_bf16_trunc_raw(acc)
        golden.extend(lap_out)

    # ---- pack + write bus beats -------------------------------------------
    with WFILE.open("w") as wf, IFILE.open("w") as jf:
        for beat in range(NBEATS):
            wbus = 0
            ibus = 0
            for i in range(CORES):
                for b in range(BLOCKS):
                    w0, w1 = w_val[beat][i][b]
                    e0 = 16 * i + 2 * b
                    wbus |= bf16_raw(w0) << (16 * e0)
                    wbus |= bf16_raw(w1) << (16 * e0 + 16)
                    i0, i1 = idx[beat][i][b]
                    base = 80 * i + 10 * b
                    ibus |= (i0 & 0x1F) << base
                    ibus |= (i1 & 0x1F) << (base + 5)
            sp_int = int(beat_meta[beat][1], 2)      # THIS beat's sparsity (its lap's)
            ibus |= (sp_int & 0x3) << 640            # Sparsity at [641:640]
            wf.write(f"{wbus:0512X}\n")              # 2048 bits
            jf.write(f"{ibus:0192X}\n")              #  768 bits

    with AFILE.open("w") as af:
        for w in range(a.nwin):
            abus = 0
            for k in range(WIN_ELEMS):
                abus |= act_raw[w][k] << (16 * k)
            af.write(f"{abus:0128X}\n")              # 512 bits

    with GFILE.open("w") as gf:
        for raw in golden:
            gf.write(f"{raw:04X}\n")                 # one bf16 per line

    laps_desc = ", ".join(f"L{L}:{s}(2:{SP_MAP[s]})" for L, s in enumerate(lap_sp))
    print(f"NWIN={a.nwin}  laps=[{laps_desc}]  total beats={NBEATS}")
    print(f"  weights.hex    : {NBEATS} beats x 2048b -> {WFILE.name}")
    print(f"  indices.hex    : {NBEATS} beats x  768b -> {IFILE.name}  (per-lap Sparsity in [641:640])")
    print(f"  activations.hex: {a.nwin} windows x 512b  -> {AFILE.name}")
    print(f"  golden.txt     : {len(golden)} rows ({nlaps} lap(s) x 64) -> {GFILE.name}")


if __name__ == "__main__":
    main()
