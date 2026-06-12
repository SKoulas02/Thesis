import random

# --- Configuration ---
ROWS              = 64      # Number of rows to generate (64 matrix rows * 32 cols / 2 elems-per-line)
ELEMENTS_PER_ROW  = 16      # Number of 4-bit indices per row
OUTPUT_FILE       = "Indices_64x16_SW.txt"

# Valid index set: {0000, 0001, 0010, 0011, 1000, 1001, 1010, 1011, 1100, 1101, 1110, 1111} -> integers 0..15
VALID_INDICES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]


def main():
    with open(OUTPUT_FILE, "w") as f:
        for _ in range(ROWS):
            row_bits = ""
            for _ in range(ELEMENTS_PER_ROW):
                idx = random.choice(VALID_INDICES)
                row_bits += f"{idx:04b}"
            f.write(row_bits + "\n")

    print(f"Wrote {ROWS} rows of {ELEMENTS_PER_ROW} 4-bit indices to '{OUTPUT_FILE}'.")
    print(f"Each line is {ELEMENTS_PER_ROW * 4} bits.")


if __name__ == "__main__":
    main()
