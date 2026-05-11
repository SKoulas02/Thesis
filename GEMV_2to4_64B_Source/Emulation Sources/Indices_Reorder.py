"""Reorder a matrix of 3-bit elements into a chunked layout.

Input  : a text file where each line is one matrix row, every element is a
         single character (3-bit value), with no separators between elements.
Output : a text file where each line holds BUS elements following the
         pattern below.

Parameters:
  - BUS    : elements per output line (output row width)
  - CHUNKS : number of chunks per output line
             chunk_size = BUS // CHUNKS

Pattern (with chunk_size = BUS // CHUNKS):
  - Process the matrix in column-blocks of chunk_size columns at a time.
  - Inside each column-block, walk matrix rows in groups of CHUNKS rows.
  - For each row-group, emit one output line composed of CHUNKS chunks:
      for k = CHUNKS-1 down to 0:
          append reverse( row[r+k][col_start : col_start + chunk_size] )
  - When all rows are consumed for one column-block, advance to the next
    column-block and restart from the top of the matrix.

Example (4x4 matrix, BUS = 4, CHUNKS = 2 -> chunk_size = 2):
  input rows (positions shown as row,col):
    00 01 02 03
    10 11 12 13
    20 21 22 23
    30 31 32 33
  output lines:
    11 10 01 00      (cols 0..1, rows 0,1)
    31 30 21 20      (cols 0..1, rows 2,3)
    13 12 03 02      (cols 2..3, rows 0,1)
    33 32 23 22      (cols 2..3, rows 2,3)
"""

from pathlib import Path

# ---- Configuration ---------------------------------------------------------
INPUT_FILE  = Path(__file__).parent / "Indices_128x32_SW.txt"
OUTPUT_FILE = Path(__file__).parent / "Indices_128x32_HW.txt"
BUS         = 128           # elements per output line
CHUNKS      = 8           # chunks per output line (chunk_size = BUS // CHUNKS)
ELEM_CHARS  = 3           # characters per element in the input file
# ---------------------------------------------------------------------------


def chunk_reorder(input_path: Path, output_path: Path,
                  bus: int, chunks: int, elem_chars: int = 1) -> None:
    if bus % chunks != 0:
        raise ValueError(
            f"BUS ({bus}) must be divisible by CHUNKS ({chunks})"
        )
    chunk_size = bus // chunks

    with input_path.open("r") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    if not lines:
        raise ValueError(f"Input file {input_path} is empty")

    line_chars = len(lines[0])
    if line_chars % elem_chars != 0:
        raise ValueError(
            f"Line width {line_chars} not a multiple of ELEM_CHARS={elem_chars}"
        )
    n_cols = line_chars // elem_chars
    n_rows = len(lines)

    if n_rows % chunks != 0:
        raise ValueError(
            f"Matrix row count ({n_rows}) must be divisible by CHUNKS={chunks}"
        )
    if n_cols % chunk_size != 0:
        raise ValueError(
            f"Column count ({n_cols}) not divisible by chunk_size={chunk_size}"
        )

    matrix = [
        [ln[i * elem_chars:(i + 1) * elem_chars] for i in range(n_cols)]
        for ln in lines
    ]

    with output_path.open("w") as f:
        for col_start in range(0, n_cols, chunk_size):
            for r in range(0, n_rows, chunks):
                out = []
                for k in range(chunks - 1, -1, -1):
                    row_chunk = matrix[r + k][col_start:col_start + chunk_size]
                    out.extend(reversed(row_chunk))
                f.write("".join(out) + "\n")

    total_lines = (n_rows // chunks) * (n_cols // chunk_size)
    print(
        f"Read {n_rows}x{n_cols} matrix from {input_path.name}; "
        f"wrote {total_lines} lines of {bus} elements "
        f"(BUS={bus}, CHUNKS={chunks}, chunk_size={chunk_size}) "
        f"to {output_path.name}."
    )


if __name__ == "__main__":
    chunk_reorder(INPUT_FILE, OUTPUT_FILE, BUS, CHUNKS, ELEM_CHARS)
