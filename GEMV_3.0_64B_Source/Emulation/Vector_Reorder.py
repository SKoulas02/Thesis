"""Reverse a bfloat16 hex vector in fixed-size chunks.

Input  : a text file with one bfloat16 element per line (4 hex chars each).
Output : a text file where each line contains CHUNK_SIZE elements taken
         from the input in order. Elements
         are concatenated with no separators.

Example (CHUNK_SIZE = 4):
  Input lines : 0, 1, 2, 3, 4, 5, 6, 7
  Output line1: 0123
  Output line2: 4567
"""

from pathlib import Path

# ---- Configuration ---------------------------------------------------------
INPUT_FILE  = Path(__file__).parent / "B_8x1_SW.txt"
OUTPUT_FILE = Path(__file__).parent / "B_8x1_HW.txt"
CHUNK_SIZE  = 4    # elements per output line
HEX_CHARS   = 4    # hex chars per bfloat16 element
# ---------------------------------------------------------------------------


def reverse_chunks(input_path: Path, output_path: Path,
                   chunk_size: int, hex_chars: int = 4) -> None:
    with input_path.open("r") as f:
        elements = [ln.strip() for ln in f if ln.strip()]

    if len(elements) % chunk_size != 0:
        raise ValueError(
            f"Element count {len(elements)} is not divisible by "
            f"CHUNK_SIZE={chunk_size}"
        )
    for i, e in enumerate(elements):
        if len(e) != hex_chars:
            raise ValueError(
                f"Element {i} has width {len(e)} (expected {hex_chars}): {e!r}"
            )

    with output_path.open("w") as f:
        for start in range(0, len(elements), chunk_size):
            chunk = elements[start:start + chunk_size]
            f.write("".join(chunk) + "\n")

    print(
        f"Read {len(elements)} elements from {input_path.name}; "
        f"wrote {len(elements) // chunk_size} lines of {chunk_size} elements "
        f"to {output_path.name}."
    )


if __name__ == "__main__":
    reverse_chunks(INPUT_FILE, OUTPUT_FILE, CHUNK_SIZE, HEX_CHARS)
