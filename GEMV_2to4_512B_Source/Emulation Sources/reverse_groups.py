from pathlib import Path

# ---- Configuration ---------------------------------------------------------
INPUT_FILE  = Path(__file__).parent / "indices_128x32.txt"
OUTPUT_FILE = Path(__file__).parent / "indices_128x32_reversed.txt"
IND_CHARS   = 3   # bits per index element
GROUP_SIZE  = 8   # elements per group to reverse
# ---------------------------------------------------------------------------


def reverse_groups(input_path: Path, output_path: Path) -> None:
    group_chars = GROUP_SIZE * IND_CHARS  # 24 chars per group

    with input_path.open("r") as f:
        lines = f.readlines()

    out_lines = []
    for line in lines:
        stripped = line.rstrip("\n")
        if not stripped:
            out_lines.append("\n")
            continue

        if len(stripped) % group_chars != 0:
            raise ValueError(
                f"Line length {len(stripped)} is not a multiple of "
                f"group size {group_chars} ({GROUP_SIZE} elements x {IND_CHARS} bits)"
            )

        groups = [stripped[i:i + group_chars] for i in range(0, len(stripped), group_chars)]
        reversed_groups = []
        for group in groups:
            elems = [group[j:j + IND_CHARS] for j in range(0, group_chars, IND_CHARS)]
            reversed_groups.append("".join(reversed(elems)))

        out_lines.append("".join(reversed_groups) + "\n")

    with output_path.open("w") as f:
        f.writelines(out_lines)

    print(f"Done. Wrote {len(out_lines)} lines to {output_path}")


if __name__ == "__main__":
    reverse_groups(INPUT_FILE, OUTPUT_FILE)
