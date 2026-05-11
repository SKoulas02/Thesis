"""Reorder a .txt of bfloat16 hex values into an M x N matrix.

Input file pattern: all elements appear in row-major order (left-to-right,
top-to-bottom). The script flattens the file and rewrites it as M rows of
N elements each.
"""

from pathlib import Path

# ---- Configuration ---------------------------------------------------------
M = 128                                  # output rows
N = 1                                     # output cols
HEX_CHARS = 4                             # 4 hex chars per bfloat16 element
INPUT_FILE  = Path(__file__).parent / "B_128x1.txt"
OUTPUT_FILE = Path(__file__).parent / f"B_{M}x{N}_reordered.txt"
# ---------------------------------------------------------------------------


def reorder(input_path: Path, output_path: Path, m: int, n: int) -> None:
    with input_path.open("r") as f:
        blob = "".join(ln.strip() for ln in f if ln.strip())

    if len(blob) % HEX_CHARS != 0:
        raise ValueError(f"Total chars {len(blob)} not a multiple of {HEX_CHARS}")

    elements = [blob[i:i + HEX_CHARS] for i in range(0, len(blob), HEX_CHARS)]

    expected = m * n
    if len(elements) != expected:
        raise ValueError(
            f"File has {len(elements)} elements, expected {expected} for {m}x{n}"
        )

    with output_path.open("w") as f:
        for r in range(m):
            row = elements[r * n:(r + 1) * n]
            f.write("".join(row) + "\n")

    print(f"Wrote {m}x{n} matrix to {output_path}")


if __name__ == "__main__":
    reorder(INPUT_FILE, OUTPUT_FILE, M, N)
