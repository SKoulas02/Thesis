"""Compare the GEMV 4.0 golden model vs the Vivado two2N_TB dump.

golden.txt : from gemv4_cosim_gen.py  (one bf16 hex per line, lane r = 8*core+block)
output.txt : from two2N_TB.vhd        (same order/format)

With the exact-in-bf16 stimulus the two files should be BIT-IDENTICAL. Any mismatch
is a wiring/packing bug (or a datapath bug), not a rounding difference. Prints the
first mismatches and a decimal/relative-error table.
"""

from pathlib import Path
import struct

HERE   = Path(__file__).parent
GOLDEN = HERE / "golden.txt"
HWOUT  = HERE / "output.txt"
REPORT = HERE / "compare_gemv4_report.txt"


def bf16_hex_to_float(h: str) -> float:
    raw = int(h.strip(), 16) & 0xFFFF
    return struct.unpack(">f", struct.pack(">I", raw << 16))[0]


def load(path: Path) -> list[str]:
    return [ln.strip().upper().zfill(4) for ln in path.open() if ln.strip()]


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

    print(f"\ncompared {n} rows  |  {mism} mismatch(es)  |  max rel error {max_rel:.4f}%")
    print("PASS (bit-exact)" if mism == 0 and len(g) == len(h) else "FAIL")
    print(f"report -> {REPORT}")


if __name__ == "__main__":
    main()
