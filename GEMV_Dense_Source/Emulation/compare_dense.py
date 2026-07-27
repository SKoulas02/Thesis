"""Compare the DENSE GEMV golden model vs the Vivado dense_TB dump.

golden.txt : from gemv_dense_gen.py  (one bf16 hex per line, lane r = 8*core+block)
output.txt : from dense_TB.vhd       (same order/format)

With the exact-in-bf16 stimulus the two files should be BIT-IDENTICAL. Any mismatch
is a wiring/packing bug (or a datapath bug), not a rounding difference. Prints the
first mismatches and a decimal/relative-error table.
"""

from pathlib import Path
import struct

HERE   = Path(__file__).parent
GOLDEN = HERE / "golden.txt"
HWOUT  = HERE / "output.txt"
TLAST  = HERE / "tlast.txt"
REPORT = HERE / "compare_dense_report.txt"


def bf16_hex_to_float(h: str) -> float:
    raw = int(h.strip(), 16) & 0xFFFF
    return struct.unpack(">f", struct.pack(">I", raw << 16))[0]


def load(path: Path) -> list[str]:
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
        print(f"\nTLAST : FAIL -- output PCs disagreed on beat(s) {bad} (fork desync)")
        return False

    tagged = [i for i, f in enumerate(flags) if f == "1"]
    print(f"\nTLAST : {len(flags)} beat(s), marker on beat(s) {tagged if tagged else 'NONE'}"
          f"  (expected [{nbeats - 1}] = the last)")

    if len(flags) != nbeats:
        print(f"TLAST : FAIL -- {len(flags)} tlast entries vs {nbeats} data beats")
        ok = False
    if len(tagged) != 1:
        print(f"TLAST : FAIL -- expected exactly 1 tagged beat, got {len(tagged)}")
        ok = False
    elif tagged[0] != nbeats - 1:
        print(f"TLAST : FAIL -- end-of-calc marker on beat {tagged[0]}, "
              f"should be the final beat {nbeats - 1}")
        ok = False

    if ok:
        print("TLAST : PASS -- marker on the final beat only")
    return ok


def main() -> None:
    g = load(GOLDEN)
    h = load(HWOUT)

    if len(g) != len(h):
        print(f"WARNING: golden has {len(g)} rows, HW has {len(h)} rows -> comparing min.")
    n = min(len(g), len(h))

    mism = 0
    max_rel = 0.0
    with REPORT.open("w") as out:
        out.write(f"{'row':>4}  {'golden':>6}  {'hw':>6}  {'gold(dec)':>12}  {'hw(dec)':>12}  {'rel%':>8}\n")
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
                    print(f"row {r:2d}: golden {gs}  hw {hs}   ({gf:g} vs {hf:g})")
            out.write(f"{r:>4}  {gs:>6}  {hs:>6}  {gf:>12.4f}  {hf:>12.4f}  {rel:>7.3f}%{flag}\n")

    data_ok = (mism == 0 and len(g) == len(h))
    print(f"\ncompared {n} rows  |  {mism} mismatch(es)  |  max rel error {max_rel:.4f}%")
    print("DATA  : PASS (bit-exact)" if data_ok else "DATA  : FAIL")

    tlast_ok = check_tlast(len(h))

    print(f"\n=== {'PASS' if (data_ok and tlast_ok) else 'FAIL'} ===")
    print(f"report -> {REPORT}")


if __name__ == "__main__":
    main()
