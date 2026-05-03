import random

# --- Configuration ---
ROWS              = 2048   # Number of rows to generate (1024 matrix rows * 256 cols / 128 elems-per-line)
ELEMENTS_PER_ROW  = 128    # Number of 3-bit indices per row
OUTPUT_FILE       = "indices_1024x512.txt"

# Valid index set: {000, 001, 010, 011, 100, 101} -> integers 0..5
VALID_INDICES = [0, 1, 2, 3, 4, 5]


def main():
    with open(OUTPUT_FILE, "w") as f:
        for _ in range(ROWS):
            row_bits = ""
            for _ in range(ELEMENTS_PER_ROW):
                idx = random.choice(VALID_INDICES)
                row_bits += f"{idx:03b}"
            f.write(row_bits + "\n")

    print(f"Wrote {ROWS} rows of {ELEMENTS_PER_ROW} 3-bit indices to '{OUTPUT_FILE}'.")
    print(f"Each line is {ELEMENTS_PER_ROW * 3} bits.")


if __name__ == "__main__":
    main()
