"""Reorder a .txt of 3-bit indices back into the original matrix.

Input file pattern (per file row of W elements):
  - W elements are split into chunks of CHUNK_SIZE elements.
  - Chunks stream in this order:
      (row 0, cols 0..C-1), (row 1, cols 0..C-1), ..., (row M-1, cols 0..C-1),
      (row 0, cols C..2C-1), ..., (row M-1, cols C..2C-1),
      ... until all N columns are written.
    Where C = CHUNK_SIZE.
  - File rows pack (W / CHUNK_SIZE) consecutive chunks each.
"""

from pathlib import Path

# ---- Configuration ---------------------------------------------------------
M           = 128                                  # matrix rows
N           = 32                                   # matrix cols
IND_CHARS   = 3                                     # bits per index element
CHUNK_SIZE  = 8                                     # elements per chunk (one row at a time)
INPUT_FILE  = Path(__file__).parent / "indices_128x32_reversed.txt"
OUTPUT_FILE = Path(__file__).parent / f"indices_{M}x{N}_reordered2.txt"
# ---------------------------------------------------------------------------


def reorder(input_path: Path, output_path: Path, m: int, n: int) -> None:
    if n % CHUNK_SIZE != 0:
        raise ValueError(f"N ({n}) not divisible by chunk size ({CHUNK_SIZE})")

    with input_path.open("r") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    line_chars = len(lines[0])
    if line_chars % IND_CHARS != 0:
        raise ValueError(f"Line width {line_chars} not a multiple of {IND_CHARS}")

    w = line_chars // IND_CHARS              # elements per file row
    if w % CHUNK_SIZE != 0:
        raise ValueError(
            f"Elements per file row ({w}) not divisible by chunk size ({CHUNK_SIZE})"
        )

    chunks_per_line = w // CHUNK_SIZE
    expected_chunks = m * (n // CHUNK_SIZE)
    expected_rows = expected_chunks // chunks_per_line
    if len(lines) != expected_rows:
        raise ValueError(
            f"File has {len(lines)} rows, expected {expected_rows} for "
            f"{m}x{n} with W={w}, CHUNK_SIZE={CHUNK_SIZE}"
        )

    matrix = [[None] * n for _ in range(m)]

    chunk_index = 0
    for line in lines:
        elems = [line[i * IND_CHARS:(i + 1) * IND_CHARS] for i in range(w)]
        for c in range(chunks_per_line):
            chunk = elems[c * CHUNK_SIZE:(c + 1) * CHUNK_SIZE]
            col_chunk = chunk_index // m
            row       = chunk_index %  m
            col_start = col_chunk * CHUNK_SIZE
            matrix[row][col_start:col_start + CHUNK_SIZE] = chunk
            chunk_index += 1

    with output_path.open("w") as f:
        for row in matrix:
            f.write("".join(row) + "\n")

    print(f"Wrote {m}x{n} matrix to {output_path}")


if __name__ == "__main__":
    reorder(INPUT_FILE, OUTPUT_FILE, M, N)
