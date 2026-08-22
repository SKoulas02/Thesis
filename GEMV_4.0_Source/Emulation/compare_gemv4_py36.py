"""Compare the GEMV 4.0 golden model vs the Vivado two2N_TB dump.

PYTHON 3.6 COMPATIBLE COPY of compare_gemv4.py, for the university server
(python3 3.6.9). The only difference is the signature of load(): the original
annotates it `-> list[str]`, and builtin-generic subscripting needs Python 3.9+.
`from __future__ import annotations` would fix it from 3.7 onward but is itself
unavailable on 3.6, so the annotation is simply dropped here.

Behaviour, output and report format are otherwise IDENTICAL to compare_gemv4.py
-- keep the two in sync if either is edited.

golden.txt : from gemv4_cosim_gen.py  (one bf16 hex per line, lane r = 8*core+block)
output.txt : from two2N_TB.vhd        (same order/format)
tlast.txt  : from two2N_TB.vhd        (one line per output beat: 1 / 0 / X)

With the exact-in-bf16 stimulus the two files should be BIT-IDENTICAL. Any mismatch
is a wiring/packing bug (or a datapath bug), not a rounding difference. Prints the
first mismatches and a decimal/relative-error table.

Uses only the standard library (struct, pathlib) -- no PyTorch needed, unlike the
generator.

    python3 compare_gemv4_py36.py
"""

from pathlib import Path
import struct

HERE   = Path(__file__).parent
GOLDEN = HERE / "golden.txt"
HWOUT  = HERE / "output.txt"
TLAST  = HERE / "tlast.txt"
REPORT = HERE / "compare_gemv4_report.txt"


def bf16_hex_to_float(h: str) -> float:
    raw = int(h.strip(), 16) & 0xFFFF
    return struct.unpack(">f", struct.pack(">I", raw << 16))[0]


def load(path):
    """Read a hex-per-line file -> list of 4-char uppercase strings.

    (Return annotation omitted on purpose: `list[str]` is a syntax-valid but
    runtime-invalid annotation before Python 3.9.)
    """
    return [ln.strip().upper().zfill(4) for ln in path.open() if ln.strip()]


def check_tlast(n_rows: int) -> bool:
    """The end-of-calculation marker must ride the FINAL output beat and no other.

    TLAST is an AXIS sideband, so it never appears in output.txt -- a data-only
    compare passes happily while the marker sits on the wrong beat. Downstream
    that tells the HBM write engine the transfer ended early.
    """
    if not TLAST.exists():
        print("\nTLAST : tlast.txt not found -- rebuild/rerun the TB to enable this check")
        return True                      # not a failure, just uninstrumented

    flags = [ln.strip() for ln in TLAST.open() if ln.strip()]
    nbeats = n_rows // 64
    ok = True

    if "X" in flags:
        bad = [i for i, f in enumerate(flags) if f == "X"]
        print("\nTLAST : FAIL -- output PCs disagreed on beat(s) {} (fork desync)".format(bad))
        return False

    tagged = [i for i, f in enumerate(flags) if f == "1"]
    print("\nTLAST : {} beat(s), marker on beat(s) {}  (expected [{}] = the last)".format(
        len(flags), tagged if tagged else "NONE", nbeats - 1))

    if len(flags) != nbeats:
        print("TLAST : FAIL -- {} tlast entries vs {} data beats".format(len(flags), nbeats))
        ok = False
    if len(tagged) != 1:
        print("TLAST : FAIL -- expected exactly 1 tagged beat, got {}".format(len(tagged)))
        ok = False
    elif tagged[0] != nbeats - 1:
        print("TLAST : FAIL -- end-of-calc marker on beat {}, should be the final beat {}".format(
            tagged[0], nbeats - 1))
        ok = False

    if ok:
        print("TLAST : PASS -- marker on the final beat only")
    return ok


def main() -> None:
    g = load(GOLDEN)
    h = load(HWOUT)

    if len(g) != len(h):
        print("WARNING: golden has {} rows, HW has {} rows -> comparing min.".format(len(g), len(h)))
    n = min(len(g), len(h))

    mism = 0
    max_rel = 0.0
    with REPORT.open("w") as out:
        out.write("{:>4}  {:>6}  {:>6}  {:>12}  {:>12}  {:>8}\n".format(
            "row", "golden", "hw", "gold(dec)", "hw(dec)", "rel%"))
        out.write("-" * 60 + "\n")
        for r in range(n):
            gs, hs = g[r], h[r]
            gf, hf = bf16_hex_to_float(gs), bf16_hex_to_float(hs)
            diff = abs(gf - hf)
            rel = (diff / abs(gf) * 100.0) if gf != 0 else (0.0 if hf == 0 else float("inf"))
            max_rel = max(max_rel, rel)
            flag = "" if gs == hs else "  <-- MISMATCH"
            if gs != hs:
                mism += 1
                if mism <= 8:
                    print("row {:2d}: golden {}  hw {}   ({:g} vs {:g})".format(r, gs, hs, gf, hf))
            out.write("{:>4}  {:>6}  {:>6}  {:>12.4f}  {:>12.4f}  {:>7.3f}%{}\n".format(
                r, gs, hs, gf, hf, rel, flag))

    data_ok = (mism == 0 and len(g) == len(h))
    print("\ncompared {} rows  |  {} mismatch(es)  |  max rel error {:.4f}%".format(n, mism, max_rel))
    print("DATA  : PASS (bit-exact)" if data_ok else "DATA  : FAIL")

    tlast_ok = check_tlast(len(h))

    print("\n=== {} ===".format("PASS" if (data_ok and tlast_ok) else "FAIL"))
    print("report -> {}".format(REPORT))


if __name__ == "__main__":
    main()
