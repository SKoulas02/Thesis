"""Reorder a bfloat16 hex matrix into a chunked layout.

Input  : a text file where each line is one matrix row, every element is
         exactly 4 hex chars, with no separators between elements.
Output : a text file where each line holds CHUNK_SIZE elements (also as
         contiguous 4-hex-char values) following the pattern below.

Pattern (with HALF = CHUNK_SIZE // 2):
  - Process the matrix in column-blocks of HALF columns at a time.
  - Inside each column-block, walk matrix rows in pairs (r, r+1).
  - For each pair, emit one output line:
        reverse( row[r+1][col_start : col_start+HALF] )
      + reverse( row[r  ][col_start : col_start+HALF] )

Example (8x4 matrix, CHUNK_SIZE = 4, HALF = 2):
  rows 0,1 / cols 0..1 -> 11 10 01 00
  rows 2,3 / cols 0..1 -> 31 30 21 20
  ...
  rows 0,1 / cols 2..3 -> 13 12 03 02
  ...
"""

from pathlib import Path

# ---- Configuration ---------------------------------------------------------
INPUT_FILE  = Path(__file__).parent / "A_128x64_SW.txt"
OUTPUT_FILE = Path(__file__).parent / "A_128x64_HW.txt"
CHUNK_SIZE  = 32          # elements per output line (must be even)
HEX_CHARS   = 4           # hex chars per bfloat16 element
# ---------------------------------------------------------------------------


def chunk_reorder(input_path: Path, output_path: Path,
                  chunk_size: int, hex_chars: int = 4) -> None:
    if chunk_size % 2 != 0:
        raise ValueError(f"CHUNK_SIZE must be even (got {chunk_size})")
    half = chunk_size // 2

    with input_path.open("r") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    if not lines:
        raise ValueError(f"Input file {input_path} is empty")

    line_chars = len(lines[0])
    if line_chars % hex_chars != 0:
        raise ValueError(
            f"Line width {line_chars} not a multiple of {hex_chars}"
        )
    n_cols = line_chars // hex_chars
    n_rows = len(lines)

    if n_rows % 2 != 0:
        raise ValueError(f"Matrix row count must be even (got {n_rows})")
    if n_cols % half != 0:
        raise ValueError(
            f"Column count {n_cols} not divisible by HALF={half}"
        )

    matrix = [
        [ln[i * hex_chars:(i + 1) * hex_chars] for i in range(n_cols)]
        for ln in lines
    ]

    with output_path.open("w") as f:
        for col_start in range(0, n_cols, half):
            for r in range(0, n_rows, 2):
                upper = matrix[r    ][col_start:col_start + half]
                lower = matrix[r + 1][col_start:col_start + half]
                out = list(reversed(lower)) + list(reversed(upper))
                f.write("".join(out) + "\n")

    total_lines = (n_rows // 2) * (n_cols // half)
    print(
        f"Read {n_rows}x{n_cols} matrix from {input_path.name}; "
        f"wrote {total_lines} lines of {chunk_size} elements "
        f"({chunk_size * hex_chars} hex chars) to {output_path.name}."
    )


if __name__ == "__main__":
    chunk_reorder(INPUT_FILE, OUTPUT_FILE, CHUNK_SIZE, HEX_CHARS)
