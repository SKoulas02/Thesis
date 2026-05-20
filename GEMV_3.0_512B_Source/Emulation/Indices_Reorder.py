from pathlib import Path

# ---- Configuration ---------------------------------------------------------
INPUT_FILE  = Path(__file__).parent / "Indices_64x16_SW.txt"
OUTPUT_FILE = Path(__file__).parent / "Indices_64x16_HW.txt"
CHUNK      = 16     # indices taken from each row per output line. Must equal the
                    # full index count per row so a row's 16 indices stay in one
                    # 64-bit matrix_fifo word (din=64 -> dout=32, 2 reads). With
                    # CHUNK=8 two different rows shared a FIFO word, so each output
                    # row received another row's selectors (GEMV came out wrong).
BUS        = 128    # total indices per output line (BUS // CHUNK = 8 rows per beat)
ELEM_CHARS = 4      # binary characters per element
# ---------------------------------------------------------------------------


def reorder(input_path: Path, output_path: Path,
            chunk: int, bus: int, elem_chars: int = 4) -> None:
    with input_path.open("r") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    if not lines:
        raise ValueError(f"Input file {input_path} is empty")

    line_chars = len(lines[0])
    if line_chars % elem_chars != 0:
        raise ValueError(f"Line width {line_chars} not a multiple of ELEM_CHARS={elem_chars}")

    n_cols = line_chars // elem_chars
    n_rows = len(lines)
    rows_per_line = bus // chunk

    if n_cols % chunk != 0:
        raise ValueError(f"Column count ({n_cols}) not divisible by CHUNK={chunk}")
    if n_rows % rows_per_line != 0:
        raise ValueError(f"Row count ({n_rows}) not divisible by rows_per_line={rows_per_line}")

    matrix = [
        [ln[i * elem_chars:(i + 1) * elem_chars] for i in range(n_cols)]
        for ln in lines
    ]

    with output_path.open("w") as f:
        for col_start in range(0, n_cols, chunk):
            for row_start in range(0, n_rows, rows_per_line):
                out = []
                for r in range(row_start, row_start + rows_per_line):
                    out.extend(matrix[r][col_start:col_start + chunk])
                f.write("".join(out) + "\n")

    total_lines = (n_rows // rows_per_line) * (n_cols // chunk)
    print(
        f"Read {n_rows}x{n_cols} matrix from {input_path.name}; "
        f"wrote {total_lines} lines to {output_path.name}. "
        f"(CHUNK={chunk}, BUS={bus})"
    )


if __name__ == "__main__":
    reorder(INPUT_FILE, OUTPUT_FILE, CHUNK, BUS, ELEM_CHARS)
