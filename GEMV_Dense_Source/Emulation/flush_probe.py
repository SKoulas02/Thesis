"""Find WHERE the hardware accumulator flushed.

Python 3.6, standard library only.

The dense engine should accumulate every beat of a lap and flush once, on the
activation TLAST. On hardware (2026-08-23) the result looks like a partial sum:
roughly constant (~700-800) regardless of lap length, while the golden value
scales with it. That is the signature of a flush after a FIXED number of beats.

This script pins the number down. For each output lane it replays the golden
datapath beat by beat and, after every beat k, truncates the accumulator to
bf16 exactly as the Accumulator IP does at flush -- then reports the k whose
partial sum equals what the hardware actually produced.

    k == beats_per_lap   -> correct behaviour
    k == some smaller n  -> the flush fired n beats in; that n is the clue
    no match             -> not a clean early flush; the datapath itself differs

Run it right after a failing hardware run, with weights.hex / activations.hex
still matching the bin/ images the host used:

    python3 flush_probe.py
"""

from pathlib import Path
import struct

HERE = Path(__file__).parent
WFILE = HERE / "weights.hex"
AFILE = HERE / "activations.hex"
HWOUT = HERE / "output.txt"

CORES, BLOCKS = 8, 8
WIN_ELEMS = 32
BEATS_WIN = WIN_ELEMS // 2      # 16


def _fp32_bits(x):
    return struct.unpack(">I", struct.pack(">f", float(x)))[0]

def _bits_fp32(b):
    return struct.unpack(">f", struct.pack(">I", b & 0xFFFFFFFF))[0]

def _bf16_rne_raw(x):
    b = _fp32_bits(x)
    lsb = (b >> 16) & 1
    b = (b + 0x7FFF + lsb) & 0xFFFFFFFF
    return (b >> 16) & 0xFFFF

def bf16_rne(x):
    return _bits_fp32(_bf16_rne_raw(x) << 16)

def bf16_val(raw):
    return _bits_fp32((raw & 0xFFFF) << 16)

def trunc_raw(s):
    """fp32 -> bf16 by truncation, what the Accumulator emits at flush."""
    return (struct.unpack(">I", struct.pack(">f", float(s)))[0] >> 16) & 0xFFFF


def load_hex(path, width_bits):
    out = []
    for ln in path.open():
        ln = ln.strip()
        if not ln:
            continue
        v = int(ln, 16)
        out.append(v)
    return out


def main():
    wbeats = load_hex(WFILE, 2048)
    awins = load_hex(AFILE, 512)
    hw = [ln.strip().upper().zfill(4) for ln in HWOUT.open() if ln.strip()]

    nwin = len(awins)
    beats_per_lap = nwin * BEATS_WIN
    nlaps = len(wbeats) // beats_per_lap

    print("stimulus : {} weight beats, {} windows (V={}), {} beats/lap, {} lap(s)".format(
        len(wbeats), nwin, nwin * WIN_ELEMS, beats_per_lap, nlaps))
    print("hardware : {} rows in output.txt ({} beat(s))".format(len(hw), len(hw) // 64))
    print("")

    # activation element k of window w
    def act(w, k):
        return bf16_val((awins[w] >> (16 * k)) & 0xFFFF)

    # weight element e of beat b   (e = 16*core + 2*block (+1))
    def wgt(b, e):
        return bf16_val((wbeats[b] >> (16 * e)) & 0xFFFF)

    print("lane :   flush after k beats   (lap is {} beats)".format(beats_per_lap))
    print("-----------------------------------------------")

    found = {}
    lanes_to_probe = [0, 1, 2, 8, 63]     # a spread across cores and blocks
    for lane in lanes_to_probe:
        if lane >= len(hw):
            continue
        core, blk = lane // BLOCKS, lane % BLOCKS
        e0 = 16 * core + 2 * blk
        target = hw[lane]

        acc = 0.0
        hit = None
        for k in range(beats_per_lap):
            win, c = k // BEATS_WIN, k % BEATS_WIN
            w0 = wgt(k, e0)
            w1 = wgt(k, e0 + 1)
            p0 = bf16_rne(w0 * act(win, 2 * c))
            p1 = bf16_rne(w1 * act(win, 2 * c + 1))
            acc += float(bf16_rne(p0 + p1))
            if "{:04X}".format(trunc_raw(acc)) == target and hit is None:
                hit = k + 1
        found[lane] = hit
        if hit is None:
            print("  {:2d} : NO MATCH        hw={}  (not a clean early flush)".format(lane, target))
        elif hit == beats_per_lap:
            print("  {:2d} : {:4d}  <-- correct (full lap)".format(lane, hit))
        else:
            print("  {:2d} : {:4d}  <-- EARLY, {:.1f}% of the lap".format(
                lane, hit, 100.0 * hit / beats_per_lap))

    vals = [v for v in found.values() if v is not None]
    print("")
    if not vals:
        print("VERDICT: no lane matches any partial sum.")
        print("         The hardware result is not a truncated accumulation of this")
        print("         stimulus -- suspect the data path or the output packing, not")
        print("         the flush timing.")
    elif all(v == beats_per_lap for v in vals):
        print("VERDICT: every probed lane flushed at the full lap -- flush timing is fine.")
    elif len(set(vals)) == 1:
        print("VERDICT: all probed lanes flushed after the SAME {} beats "
              "(lap = {}).".format(vals[0], beats_per_lap))
        print("         A single early flush point shared by every lane points at the")
        print("         control path (last_win / counter_lock), not at the datapath.")
    else:
        print("VERDICT: lanes flushed at DIFFERENT beats: {}".format(sorted(set(vals))))
        print("         Per-lane divergence points at the cores desyncing, not at a")
        print("         single mis-timed flush.")


if __name__ == "__main__":
    main()
