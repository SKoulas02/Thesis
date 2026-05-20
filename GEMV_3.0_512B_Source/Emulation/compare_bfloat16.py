import struct
import sys

# ── Configure paths here ──────────────────────────────────────────────────────
SW_FILE  = r"C:\Koulas\ECE\Thesis\Code\GEMV_3.0_512B_Source\Emulation\C_64x1_SW.txt"
HW_FILE  = r"C:\Koulas\ECE\Thesis\Code\GEMV_3.0_512B_Source\Emulation\Output_2to4_512B.txt"
OUT_FILE = r"C:\Koulas\ECE\Thesis\Code\GEMV_3.0_512B_Source\Emulation\compare_output.txt"
# ─────────────────────────────────────────────────────────────────────────────


def bf16_hex_to_float(hex_str: str) -> float:
    """Convert a 4-character bfloat16 hex string to a Python float."""
    raw = int(hex_str.strip(), 16)
    # Bfloat16 = upper 16 bits of IEEE 754 float32; pad with 0x0000 mantissa bits
    float32_bits = raw << 16
    return struct.unpack(">f", struct.pack(">I", float32_bits))[0]


def load_hex_column(path: str) -> list[float]:
    """Read a file with either 'HEX' or 'index  HEX' rows and return floats."""
    values: list[float] = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            # Support both single-column (just hex) and two-column (index hex)
            hex_str = parts[-1]
            values.append(bf16_hex_to_float(hex_str))
    return values


def main():
    sw = load_hex_column(SW_FILE)
    hw = load_hex_column(HW_FILE)

    if len(sw) != len(hw):
        print(f"Warning: SW has {len(sw)} rows, HW has {len(hw)} rows — truncating to shorter.")
        n = min(len(sw), len(hw))
        sw, hw = sw[:n], hw[:n]

    with open(OUT_FILE, "w") as out:
        out.write(f"{'SW (dec)':>15}  {'HW (dec)':>15}  {'|SW - HW|':>15}\n")
        out.write("-" * 51 + "\n")
        for s, h in zip(sw, hw):
            diff = abs(s - h)
            out.write(f"{s:>15.6f}  {h:>15.6f}  {diff:>15.6f}\n")

    print(f"Done. {len(sw)} rows written to:\n  {OUT_FILE}")


if __name__ == "__main__":
    main()
