import torch

# --- Configuration ---
ROWS              = 8      # Number of rows to generate
ELEMENTS_PER_ROW  = 1      # Number of bfloat16 elements per row
VALUE_MIN         = 0.0    # Minimum value (inclusive)
VALUE_MAX         = 1.5    # Maximum value (exclusive)
OUTPUT_FILE       = "B_8x1_SW.txt"


def main():
    # Generate random fp32 in [VALUE_MIN, VALUE_MAX), then cast to bfloat16
    fp32 = torch.rand(ROWS, ELEMENTS_PER_ROW) * (VALUE_MAX - VALUE_MIN) + VALUE_MIN
    bf16 = fp32.to(torch.bfloat16)

    with open(OUTPUT_FILE, "w") as f:
        for r in range(ROWS):
            row_hex = ""
            for c in range(ELEMENTS_PER_ROW):
                raw_bits = bf16[r, c].view(torch.short).item() & 0xFFFF
                row_hex += f"{raw_bits:04X}"
            f.write(row_hex + "\n")

    print(f"Wrote {ROWS} rows of {ELEMENTS_PER_ROW} bfloat16 elements to '{OUTPUT_FILE}'.")
    print(f"Each line is {ELEMENTS_PER_ROW * 4} hex characters ({ELEMENTS_PER_ROW * 16} bits).")


if __name__ == "__main__":
    main()
