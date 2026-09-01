"""Large synthetic stimulus for SPARSE TIMING runs only. Python 3.6, stdlib only.

WHY A SEPARATE GENERATOR
gemv4_cosim_gen.py computes the golden model, which is O(beats x 64) in pure
Python -- fine for the thousands of beats correctness needs, hopeless for the
millions timing needs. This one writes the per-PC .bin images directly and
computes nothing.

WHY THAT IS LEGITIMATE
The sparse datapath's TIMING is data-independent:
  * the 32:1 gather is a mux -- its delay does not depend on which index it
    selects, only on the fact that it selects;
  * the Xilinx FP operators are configured with Maximum_Latency, so
    multiply/add/accumulate take a fixed number of cycles whatever the operands;
  * there is no data-dependent control flow anywhere in the engine.
The one thing that DOES change the run length is the sparsity code, because it
sets the freeze (32/M cycles per window) -- and that is an explicit argument
here, planted in every index beat exactly where the engine reads it.

So the VALUES cannot change how long a run takes; only the beat COUNTS can, and
those are computed from --sparsity/--nwin/--nlaps below.

Correctness therefore stays the job of gemv4_cosim_gen.py + compare_gemv4_py36.py
on the small cases (all four sparsities plus runtime reconfiguration, all
bit-exact on hardware 2026-08-26). This file writes NO golden.txt so nobody can
mistake its output for a verifiable run.

The fill is a tiled pattern of valid bf16 values (never NaN/Inf) and valid 5-bit
indices -- not because timing would care, but so a stray correctness check on
this data fails loudly rather than producing garbage that looks plausible.

SHAPE (must match what host_sparse.cpp derives, or it refuses to run):
    n_act_beats    = nwin                    (V = 32 * nwin elements)
    freeze         = 32 / M                  (8 / 4 / 2 / 1)
    beats_per_lap  = nwin * freeze
    n_weight_beats = nlaps * beats_per_lap   (= n_index_beats; they are joined)
    output beats   = nlaps

SPARSITY PLACEMENT: bits [641:640] of the joined 768-bit index word. Index PC2
carries joined bits [767:512], so the code sits at PC2 bits [129:128] = byte 16,
low 2 bits. host_sparse.cpp reads it back from exactly there.

USAGE
    python3 gen_timing_stimulus.py --sparsity 00 --nwin 32 --nlaps 512
"""

from pathlib import Path
import argparse

HERE = Path(__file__).parent
BIN = HERE / "bin"

PC_BITS = 256
PC_BYTES = PC_BITS // 8            # 32 -- one 256-bit pseudo-channel beat
W_PCS, IND_PCS, A_PCS = 8, 3, 2
CORES, BLOCKS = 8, 8
W_PER_CORE = 16                    # 2 weights x 8 blocks
IND_PER_CORE_BITS = 80             # 16 indices x 5 bits
IND_BITS = 5
SPARSITY_BIT = 640
HBM_PC_BYTES = 256 * 1024 * 1024   # one U280 pseudo-channel

SP_MAP = {"00": 4, "01": 8, "10": 16, "11": 32}

# A few exact-in-bf16 small integers, little-endian 16-bit each.
# 0x4040=3.0, 0x4080=4.0, 0x40A0=5.0, 0x3F80=1.0
W_PATTERN = bytes([0x40, 0x40, 0x80, 0x40, 0xA0, 0x40, 0x80, 0x3F]) * 4   # 32 bytes


def build_index_pcs(sp_code):
    """One index beat -> three 32-byte PC chunks, with the sparsity code in PC2.

    Indices cycle 0..31 so the gather actually reaches every activation element.
    Timing does not depend on that, but a degenerate all-zero index field would
    make any accidental correctness check pass for the wrong reason.
    """
    ibus = 0
    for core in range(CORES):
        for blk in range(BLOCKS):
            for slot in range(2):
                flat = W_PER_CORE * core + 2 * blk + slot     # 0..127
                idx = flat % 32
                bit = (IND_PER_CORE_BITS * core) + (2 * IND_BITS * blk) + (IND_BITS * slot)
                ibus |= (idx & 0x1F) << bit
    ibus |= (int(sp_code, 2) & 0x3) << SPARSITY_BIT
    mask = (1 << PC_BITS) - 1
    return [((ibus >> (PC_BITS * i)) & mask).to_bytes(PC_BYTES, "little")
            for i in range(IND_PCS)]


def write_stream(prefix, index, chunk, nbeats):
    """Write one per-PC image: nbeats copies of a 32-byte chunk."""
    path = BIN / "{}{}.bin".format(prefix, index)
    total = nbeats * PC_BYTES
    if total > HBM_PC_BYTES:
        raise SystemExit(
            "{}: {} bytes exceeds one HBM pseudo-channel ({} MB). "
            "Reduce --nlaps.".format(path.name, total, HBM_PC_BYTES // 1024 // 1024))
    # Build once, write once -- tiling a bytes object is instant even at 100s of MB.
    path.write_bytes(chunk * nbeats)
    return total


def main():
    ap = argparse.ArgumentParser(description="Large synthetic SPARSE stimulus for timing runs")
    ap.add_argument("--sparsity", default="00", choices=list(SP_MAP),
                    help="00=2:4 01=2:8 10=2:16 11=2:32 -- sets the freeze, so it "
                         "sets the beat count and therefore the run length")
    ap.add_argument("--nwin", type=int, default=32, help="activation windows; V = 32*nwin")
    ap.add_argument("--nlaps", type=int, required=True, help="laps = output beats")
    ap.add_argument("--freq-mhz", type=float, default=300.0,
                    help="COSMETIC ONLY -- scales the 'ideal kernel' line printed below. It "
                         "does NOT affect the generated images in any way; they are "
                         "byte-identical whatever you pass. Default 300 because both the "
                         "floorplanned sparse build and dense close at 300 MHz.")
    a = ap.parse_args()

    M = SP_MAP[a.sparsity]
    freeze = 32 // M
    beats_per_lap = a.nwin * freeze
    n_weight_beats = a.nlaps * beats_per_lap
    n_act_beats = a.nwin

    BIN.mkdir(exist_ok=True)

    wbytes = 0
    for i in range(W_PCS):
        wbytes += write_stream("weights_pc", i, W_PATTERN, n_weight_beats)

    ind_chunks = build_index_pcs(a.sparsity)
    ibytes = 0
    for i in range(IND_PCS):
        ibytes += write_stream("ind_pc", i, ind_chunks[i], n_weight_beats)

    abytes = 0
    for i in range(A_PCS):
        abytes += write_stream("act_pc", i, W_PATTERN, n_act_beats)

    # Cheap self-check: the code must be readable back exactly where the host
    # will look for it. Costs nothing and catches a packing regression here
    # rather than as a wrong answer on the card.
    got = (ind_chunks[2][16] & 0x3)
    want = int(a.sparsity, 2)
    if got != want:
        raise SystemExit("sparsity code landed wrong: PC2 byte 16 = {:02b}, expected {}"
                         .format(got, a.sparsity))

    ideal_us = n_weight_beats * (1000.0 / a.freq_mhz) / 1000.0

    print("SPARSE TIMING stimulus (no golden -- correctness is NOT checkable on this data)")
    print("  sparsity       = {} (2:{}), freeze {} cyc/window".format(a.sparsity, M, freeze))
    print("  V              = {} elements ({} windows)".format(a.nwin * 32, a.nwin))
    print("  beats/lap      = {}".format(beats_per_lap))
    print("  laps           = {}   -> {} output beats, {} rows".format(
        a.nlaps, a.nlaps, a.nlaps * 64))
    print("  n_weight_beats = {}   (= n_index_beats)".format(n_weight_beats))
    print("  n_act_beats    = {}".format(n_act_beats))
    print("  images         = {:.1f} MB weights + {:.1f} MB indices + {:.3f} MB activations".format(
        wbytes / 1e6, ibytes / 1e6, abytes / 1e6))
    print("  sparsity code  = verified at ind_pc2 byte 16 = {:02b}".format(got))
    print("  ideal kernel   = {:.3f} us at {:.0f} MHz (1 weight beat/clock)".format(
        ideal_us, a.freq_mhz))
    print("                   ^ informational only -- --freq-mhz does not change the data")
    print("")
    print("  NOTE: golden.txt is untouched and does NOT describe this data.")
    print("        Do not run compare_gemv4_py36.py against a timing run.")


if __name__ == "__main__":
    main()
