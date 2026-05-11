"""Reorder a .txt of bfloat16 hex values back into the original matrix.

Input file pattern (per file row of W elements):
  - First half (W/2 elements) -> chunk of one matrix row
  - Second half (W/2 elements) -> chunk of the next matrix row
File rows iterate matrix rows in pairs (0,1), (2,3), ..., (M-2,M-1) for the
first column-chunk; then the cycle repeats for the next column-chunk until
all N columns are filled.
"""

from pathlib import Path

# ---- Configuration ---------------------------------------------------------
M = 128                                   # matrix rows
N = 64                                    # matrix cols
HEX_CHARS = 4                             # 4 hex chars per bfloat16 element
INPUT_FILE  = Path(__file__).parent / "A_128x64.txt"
OUTPUT_FILE = Path(__file__).parent / f"A_{M}x{N}_reordered.txt"
# ---------------------------------------------------------------------------


def reorder(input_path: Path, output_path: Path, m: int, n: int) -> None:
    if m % 2 != 0:
        raise ValueError(f"M must be even (got {m})")

    with input_path.open("r") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    line_chars = len(lines[0])
    if line_chars % HEX_CHARS != 0:
        raise ValueError(f"Line width {line_chars} not a multiple of {HEX_CHARS}")

    w = line_chars // HEX_CHARS              # elements per file row
    if w % 2 != 0:
        raise ValueError(f"Elements per file row must be even (got {w})")

    half = w // 2                            # elements per matrix-row chunk
    if n % half != 0:
        raise ValueError(f"N ({n}) not divisible by half-row width ({half})")

    expected_rows = (m * n) // w
    if len(lines) != expected_rows:
        raise ValueError(
            f"File has {len(lines)} rows, expected {expected_rows} for "
            f"{m}x{n} with W={w}"
        )

    rows_per_cycle = m // 2
    matrix = [[None] * n for _ in range(m)]

    for r, line in enumerate(lines):
        elems = [line[i * HEX_CHARS:(i + 1) * HEX_CHARS] for i in range(w)]
        first_half, second_half = elems[:half], elems[half:]

        cycle = r // rows_per_cycle
        idx_in_cycle = r % rows_per_cycle
        row_a = idx_in_cycle * 2
        row_b = row_a + 1
        col_start = cycle * half

        matrix[row_a][col_start:col_start + half] = first_half
        matrix[row_b][col_start:col_start + half] = second_half

    with output_path.open("w") as f:
        for row in matrix:
            f.write("".join(row) + "\n")

    print(f"Wrote {m}x{n} matrix to {output_path}")


if __name__ == "__main__":
    reorder(INPUT_FILE, OUTPUT_FILE, M, N)
