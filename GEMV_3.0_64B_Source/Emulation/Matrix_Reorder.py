"""Reorder a bfloat16 hex matrix into a chunked layout.

Input  : a text file where each line is one matrix row, every element is
         exactly 4 hex chars, with no separators between elements.
Output : a text file where each line holds CHUNK_SIZE elements (also as
         contiguous 4-hex-char values) following the pattern below.

Pattern:
  - Process the matrix in column-blocks of CHUNK_SIZE columns at a time.
  - Inside each column-block, walk every matrix row individually.
  - For each row, emit one output line:
        row[r][col_start : col_start+CHUNK_SIZE]

Example (8x4 matrix, CHUNK_SIZE = 4):
  row 0 / cols 0..3 -> 00 01 02 03
  row 1 / cols 0..3 -> 10 11 12 13
  ...
  row 0 / cols 4..7 -> 04 05 06 07
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

    if n_cols % chunk_size != 0:
        raise ValueError(
            f"Column count {n_cols} not divisible by CHUNK_SIZE={chunk_size}"
        )

    matrix = [
        [ln[i * hex_chars:(i + 1) * hex_chars] for i in range(n_cols)]
        for ln in lines
    ]

    with output_path.open("w") as f:
        for col_start in range(0, n_cols, chunk_size):
            for r in range(n_rows):
                out = matrix[r][col_start:col_start + chunk_size]
                f.write("".join(out) + "\n")

    total_lines = n_rows * (n_cols // chunk_size)
    print(
        f"Read {n_rows}x{n_cols} matrix from {input_path.name}; "
        f"wrote {total_lines} lines of {chunk_size} elements "
        f"({chunk_size * hex_chars} hex chars) to {output_path.name}."
    )


if __name__ == "__main__":
    chunk_reorder(INPUT_FILE, OUTPUT_FILE, CHUNK_SIZE, HEX_CHARS)
