"""Large synthetic stimulus for TIMING runs only. Python 3.6, stdlib only.

WHY A SEPARATE GENERATOR
gemv_dense_gen.py computes the golden model, which is O(beats x 64) in pure
Python -- fine for the thousands of beats correctness needs, hopeless for the
millions timing needs. This one writes the per-PC .bin images directly and
computes nothing.

WHY THAT IS LEGITIMATE
The dense datapath is positional: at beat c of a window every block uses
activation elements 2c and 2c+1, regardless of the values. The Xilinx FP
operators are configured with Maximum_Latency, so multiply/add/accumulate take
a fixed number of cycles whatever the operands. There is no data-dependent
control flow anywhere in the engine. So the VALUES cannot change how long a run
takes -- only the beat COUNTS can.

Correctness therefore stays the job of gemv_dense_gen.py + compare_dense_py36.py
on the small cases. This file is for throughput only, and it deliberately writes
no golden.txt so nobody can mistake its output for a verifiable run.

The fill is a tiled pattern of valid bf16 values (never NaN/Inf) -- not because
timing would care, but so a stray correctness check on this data fails loudly
rather than producing garbage that looks plausible.

SHAPE (must match what the host derives, or it refuses to run):
    n_act_beats    = nwin                    (V = 32 * nwin elements)
    beats_per_lap  = nwin * 16               (dense: V/2)
    n_weight_beats = nlaps * beats_per_lap
    output beats   = nlaps

USAGE
    python3 gen_timing_stimulus.py --nwin 32 --nlaps 4096
    python3 hex_to_bin.py            # NOT needed -- this writes bin/ directly
"""

from pathlib import Path
import argparse

HERE = Path(__file__).parent
BIN = HERE / "bin"

PC_BYTES = 32          # 256-bit pseudo-channel beat
W_PCS, A_PCS = 8, 2
BEATS_PER_WINDOW = 16
HBM_PC_BYTES = 256 * 1024 * 1024   # one U280 pseudo-channel

# A few exact-in-bf16 small integers, little-endian 16-bit each.
# 0x4040=3.0, 0x4080=4.0, 0x40A0=5.0, 0x3F80=1.0
PATTERN = bytes([0x40, 0x40, 0x80, 0x40, 0xA0, 0x40, 0x80, 0x3F]) * 4   # 32 bytes


def write_stream(prefix, index, nbeats):
    """Write one per-PC image of nbeats x 32 bytes, tiled from PATTERN."""
    path = BIN / "{}{}.bin".format(prefix, index)
    total = nbeats * PC_BYTES
    if total > HBM_PC_BYTES:
        raise SystemExit(
            "{}: {} bytes exceeds one HBM pseudo-channel ({} MB). "
            "Reduce --nlaps.".format(path.name, total, HBM_PC_BYTES // 1024 // 1024))
    # Build once, write once -- tiling a bytes object is instant even at 100s of MB.
    chunk = PATTERN * nbeats
    path.write_bytes(chunk)
    return total


def main():
    ap = argparse.ArgumentParser(description="Large synthetic stimulus for timing runs")
    ap.add_argument("--nwin", type=int, default=32, help="activation windows; V = 32*nwin")
    ap.add_argument("--nlaps", type=int, required=True, help="laps = output beats")
    a = ap.parse_args()

    beats_per_lap = a.nwin * BEATS_PER_WINDOW
    n_weight_beats = a.nlaps * beats_per_lap
    n_act_beats = a.nwin

    BIN.mkdir(exist_ok=True)
    wbytes = 0
    for i in range(W_PCS):
        wbytes += write_stream("weights_pc", i, n_weight_beats)
    abytes = 0
    for i in range(A_PCS):
        abytes += write_stream("act_pc", i, n_act_beats)

    ideal_us = n_weight_beats * (1000.0 / 300.0) / 1000.0

    print("TIMING stimulus (no golden -- correctness is NOT checkable on this data)")
    print("  V              = {} elements ({} windows)".format(a.nwin * 32, a.nwin))
    print("  beats/lap      = {}".format(beats_per_lap))
    print("  laps           = {}   -> {} output beats, {} rows".format(
        a.nlaps, a.nlaps, a.nlaps * 64))
    print("  n_weight_beats = {}".format(n_weight_beats))
    print("  n_act_beats    = {}".format(n_act_beats))
    print("  images         = {:.1f} MB weights + {:.3f} MB activations".format(
        wbytes / 1e6, abytes / 1e6))
    print("  ideal kernel   = {:.3f} us at 300 MHz (1 weight beat/clock)".format(ideal_us))
    print("")
    print("  NOTE: golden.txt is untouched and does NOT describe this data.")
    print("        Do not run compare_dense_py36.py against a timing run.")


if __name__ == "__main__":
    main()
