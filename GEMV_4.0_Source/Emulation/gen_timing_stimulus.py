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
    return write_segments(prefix, index, [(chunk, nbeats)])


def write_segments(prefix, index, segments):
    """Write one per-PC image from a list of (32-byte chunk, beat count).

    A single-element list is the ordinary uniform-sparsity case. Several
    elements produce a MIXED matrix: the beats of segment 0 first, then
    segment 1, and so on. Because the sparsity code rides inside the index
    beat itself, laying the index PCs out this way IS the schedule -- the
    engine re-reads the code at every window boundary and re-derives its
    freeze length from it (top_module_2N.vhd, `case sparsity_next`).
    """
    path = BIN / "{}{}.bin".format(prefix, index)
    nbeats = sum(n for _, n in segments)
    total = nbeats * PC_BYTES
    if total > HBM_PC_BYTES:
        raise SystemExit(
            "{}: {} bytes exceeds one HBM pseudo-channel ({} MB). "
            "Reduce --nlaps.".format(path.name, total, HBM_PC_BYTES // 1024 // 1024))
    # Build once, write once -- tiling a bytes object is instant even at 100s of MB.
    with path.open("wb") as f:
        for chunk, n in segments:
            f.write(chunk * n)
    return total


def main():
    ap = argparse.ArgumentParser(description="Large synthetic SPARSE stimulus for timing runs")
    ap.add_argument("--sparsity", default="00", choices=list(SP_MAP),
                    help="00=2:4 01=2:8 10=2:16 11=2:32 -- sets the freeze, so it "
                         "sets the beat count and therefore the run length")
    ap.add_argument("--nwin", type=int, default=32, help="activation windows; V = 32*nwin")
    ap.add_argument("--nlaps", type=int, help="laps = output beats")
    ap.add_argument("--mix", default=None, metavar="CODE:LAPS,...",
                    help="MIXED-SPARSITY matrix: split it into consecutive segments, "
                         "each with its own sparsity. e.g. "
                         "--mix 00:4096,01:4096,10:4096,11:4096 gives four equal-row "
                         "parts at 2:4 / 2:8 / 2:16 / 2:32. One lap = 64 rows = one "
                         "output beat, and sparsity MUST hold constant across a lap "
                         "because a lap is one row's complete dot product -- the host "
                         "rejects a mid-lap change. Mutually exclusive with "
                         "--sparsity/--nlaps.")
    ap.add_argument("--freq-mhz", type=float, default=300.0,
                    help="COSMETIC ONLY -- scales the 'ideal kernel' line printed below. It "
                         "does NOT affect the generated images in any way; they are "
                         "byte-identical whatever you pass. Default 300 because both the "
                         "floorplanned sparse build and dense close at 300 MHz.")
    a = ap.parse_args()

    # ---- resolve the schedule: one segment, or several -----------------
    if a.mix:
        if a.nlaps is not None:
            raise SystemExit("--mix and --nlaps are mutually exclusive")
        plan = []
        for part in a.mix.split(","):
            part = part.strip()
            if ":" not in part:
                raise SystemExit("bad --mix entry {!r}; want CODE:LAPS".format(part))
            code, laps = part.split(":", 1)
            code = code.strip()
            if code not in SP_MAP:
                raise SystemExit("bad sparsity code {!r} in --mix; want one of {}"
                                 .format(code, "/".join(sorted(SP_MAP))))
            laps = int(laps)
            if laps <= 0:
                raise SystemExit("--mix segment {!r} needs a positive lap count".format(part))
            plan.append((code, laps))
        if len(plan) < 2:
            raise SystemExit("--mix needs at least two segments; use --sparsity for one")
    else:
        if a.nlaps is None:
            raise SystemExit("give --nlaps (single sparsity) or --mix (mixed matrix)")
        plan = [(a.sparsity, a.nlaps)]

    # per segment: beats/lap follows ITS sparsity, so a 2:4 segment costs 8x
    # the beats of a 2:32 segment for the same number of rows.
    seg = []
    for code, laps in plan:
        freeze_s = 32 // SP_MAP[code]
        bpl = a.nwin * freeze_s
        seg.append(dict(code=code, laps=laps, freeze=freeze_s, bpl=bpl,
                        beats=laps * bpl, rows=laps * 64))

    n_weight_beats = sum(x["beats"] for x in seg)
    total_laps = sum(x["laps"] for x in seg)
    total_rows = sum(x["rows"] for x in seg)
    n_act_beats = a.nwin
    M = SP_MAP[seg[0]["code"]]
    freeze = seg[0]["freeze"]
    beats_per_lap = seg[0]["bpl"]

    BIN.mkdir(exist_ok=True)

    wbytes = 0
    for i in range(W_PCS):
        wbytes += write_stream("weights_pc", i, W_PATTERN, n_weight_beats)

    # the index images carry the schedule
    chunks_by_code = {}
    for x in seg:
        if x["code"] not in chunks_by_code:
            chunks_by_code[x["code"]] = build_index_pcs(x["code"])
    ibytes = 0
    for i in range(IND_PCS):
        ibytes += write_segments("ind_pc", i,
                                 [(chunks_by_code[x["code"]][i], x["beats"]) for x in seg])
    ind_chunks = chunks_by_code[seg[0]["code"]]

    abytes = 0
    for i in range(A_PCS):
        abytes += write_stream("act_pc", i, W_PATTERN, n_act_beats)

    # Cheap self-check: the code must be readable back exactly where the host
    # will look for it. Costs nothing and catches a packing regression here
    # rather than as a wrong answer on the card.
    for x in seg:
        got_x = (chunks_by_code[x["code"]][2][16] & 0x3)
        if got_x != int(x["code"], 2):
            raise SystemExit("sparsity code landed wrong: PC2 byte 16 = {:02b}, expected {}"
                             .format(got_x, x["code"]))
    got = (ind_chunks[2][16] & 0x3)

    ideal_us = n_weight_beats * (1000.0 / a.freq_mhz) / 1000.0

    print("SPARSE TIMING stimulus (no golden -- correctness is NOT checkable on this data)")
    if len(seg) > 1:
        print("  ** MIXED-SPARSITY MATRIX -- {} segments **".format(len(seg)))
        print("  V              = {} elements ({} windows)".format(a.nwin * 32, a.nwin))
        print("")
        print("    {:>4}  {:<6} {:>8} {:>10} {:>12} {:>10}".format(
            "seg", "code", "freeze", "beats/lap", "laps (rows)", "beats"))
        for i, x in enumerate(seg):
            print("    {:>4}  {:<6} {:>8} {:>10} {:>12} {:>10}".format(
                i, "{} (2:{})".format(x["code"], SP_MAP[x["code"]]),
                x["freeze"], x["bpl"],
                "{} ({})".format(x["laps"], x["rows"]), x["beats"]))
        print("    {:>4}  {:<6} {:>8} {:>10} {:>12} {:>10}".format(
            "", "TOTAL", "", "", "{} ({})".format(total_laps, total_rows), n_weight_beats))
        print("")
        uni = [(c, total_rows // 64 * (a.nwin * (32 // SP_MAP[c]))) for c in ("00", "11")]
        print("  for reference, the SAME {} rows uniformly:".format(total_rows))
        for c, b in uni:
            print("    all 2:{:<3} = {:>10} beats".format(SP_MAP[c], b))
        print("  the mixed matrix costs {} beats -- between the two, as it must be."
              .format(n_weight_beats))
    else:
        print("  sparsity       = {} (2:{}), freeze {} cyc/window".format(
            seg[0]["code"], M, freeze))
        print("  V              = {} elements ({} windows)".format(a.nwin * 32, a.nwin))
        print("  beats/lap      = {}".format(beats_per_lap))
        print("  laps           = {}   -> {} output beats, {} rows".format(
            total_laps, total_laps, total_rows))
    print("  n_weight_beats = {}   (= n_index_beats)".format(n_weight_beats))
    print("  n_act_beats    = {}".format(n_act_beats))
    print("  images         = {:.1f} MB weights + {:.1f} MB indices + {:.3f} MB activations".format(
        wbytes / 1e6, ibytes / 1e6, abytes / 1e6))
    if len(seg) > 1:
        print("  sparsity codes = all {} verified at ind_pc2 byte 16 of their segments"
              .format(len(seg)))
        print("                   the host will re-read them per lap and print its own")
        print("                   schedule -- that printout is the proof the card saw them")
    else:
        print("  sparsity code  = verified at ind_pc2 byte 16 = {:02b}".format(got))
    print("  ideal kernel   = {:.3f} us at {:.0f} MHz (1 weight beat/clock)".format(
        ideal_us, a.freq_mhz))
    print("                   ^ informational only -- --freq-mhz does not change the data")
    print("")
    print("  NOTE: golden.txt is untouched and does NOT describe this data.")
    print("        Do not run compare_gemv4_py36.py against a timing run.")


if __name__ == "__main__":
    main()
