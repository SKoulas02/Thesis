"""2:4 sparse GEMV in bfloat16.

Computes y = A_sparse * x where A_sparse is a logical M x K_LOGICAL matrix
stored in 2:4 packed form: each group of 4 logical columns keeps only 2 dense
values, plus a 3-bit index identifying which 2 of the 4 positions are kept.

Inputs (one element per hex/bit field, no separators):
  A_FILE   : M rows x K_DENSE bfloat16 (4 hex chars each).
             K_DENSE = K_LOGICAL / 2.
  IND_FILE : M rows x (K_DENSE / 2) 3-bit indices (0..5).
  B_FILE   : K_LOGICAL bfloat16 elements (4 hex chars), one per line.

Output:
  OUTPUT_FILE : M bfloat16 elements (4 hex chars), one per line.

Per-operation rounding: every multiply and every add is rounded to bfloat16
(matches the hardware datapath in Design Sources/c_block_2.0.vhd).

Index encoding (low_position, high_position) within each 4-element B group,
taken from c_block_2.0.vhd lines 131-154:
  000 -> (0, 1)   001 -> (0, 2)   010 -> (0, 3)
  011 -> (1, 2)   100 -> (1, 3)   101 -> (2, 3)
"""

from pathlib import Path
import torch

# ---- Configuration ---------------------------------------------------------
A_FILE      = Path(__file__).parent / "A_128x64_reordered.txt"
IND_FILE    = Path(__file__).parent / "indices_128x32_reordered2.txt"
B_FILE      = Path(__file__).parent / "B_128x1_reordered.txt"
OUTPUT_FILE = Path(__file__).parent / "C_128x1_Exp.txt"

M           = 128   # output vector length / matrix rows
K_DENSE     = 64    # kept (non-zero) cols per row
K_LOGICAL   = 128   # logical x length (must equal 2 * K_DENSE for 2:4)

HEX_CHARS   = 4      # hex chars per bfloat16 element
IND_CHARS   = 3      # bits per index element
# ---------------------------------------------------------------------------

INDEX_TO_PAIR = {
    0: (0, 1),
    1: (0, 2),
    2: (0, 3),
    3: (1, 2),
    4: (1, 3),
    5: (2, 3),
}


def hex_to_bf16_tensor(hex_chunks: list[str]) -> torch.Tensor:
    raw = torch.tensor([int(h, 16) & 0xFFFF for h in hex_chunks], dtype=torch.int16)
    return raw.view(torch.bfloat16)


def bf16_scalar_to_hex(v: torch.Tensor) -> str:
    raw = v.view(torch.int16).item() & 0xFFFF
    return f"{raw:04X}"


def load_matrix_bf16(path: Path, rows: int, cols: int) -> torch.Tensor:
    with path.open("r") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    if len(lines) != rows:
        raise ValueError(f"{path.name}: got {len(lines)} rows, expected {rows}")
    expected_chars = cols * HEX_CHARS
    out = torch.empty((rows, cols), dtype=torch.bfloat16)
    for r, line in enumerate(lines):
        if len(line) != expected_chars:
            raise ValueError(
                f"{path.name}: line {r} width {len(line)}, expected {expected_chars}"
            )
        chunks = [line[c * HEX_CHARS:(c + 1) * HEX_CHARS] for c in range(cols)]
        out[r] = hex_to_bf16_tensor(chunks)
    return out


def load_vector_bf16(path: Path, length: int) -> torch.Tensor:
    with path.open("r") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    if len(lines) != length:
        raise ValueError(f"{path.name}: got {len(lines)} entries, expected {length}")
    for r, line in enumerate(lines):
        if len(line) != HEX_CHARS:
            raise ValueError(
                f"{path.name}: line {r} width {len(line)}, expected {HEX_CHARS}"
            )
    return hex_to_bf16_tensor(lines)


def load_indices(path: Path, rows: int, cols: int) -> list[list[int]]:
    with path.open("r") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    if len(lines) != rows:
        raise ValueError(f"{path.name}: got {len(lines)} rows, expected {rows}")
    expected_chars = cols * IND_CHARS
    matrix: list[list[int]] = []
    for r, line in enumerate(lines):
        if len(line) != expected_chars:
            raise ValueError(
                f"{path.name}: line {r} width {len(line)}, expected {expected_chars}"
            )
        row = [int(line[c * IND_CHARS:(c + 1) * IND_CHARS], 2) for c in range(cols)]
        matrix.append(row)
    return matrix


def bf16_mul(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    return (a.float() * b.float()).bfloat16()


def bf16_add(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    return (a.float() + b.float()).bfloat16()


def main() -> None:
    if K_LOGICAL != 2 * K_DENSE:
        raise ValueError("K_LOGICAL must equal 2 * K_DENSE for 2:4 sparsity.")
    pairs_per_row = K_DENSE // 2

    A   = load_matrix_bf16(A_FILE,  M, K_DENSE)         # (M, K_DENSE) bf16
    B   = load_vector_bf16(B_FILE,  K_LOGICAL)          # (K_LOGICAL,) bf16
    IND = load_indices(IND_FILE,    M, pairs_per_row)

    y = torch.empty(M, dtype=torch.bfloat16)

    for i in range(M):
        b_lo_idx = torch.empty(pairs_per_row, dtype=torch.long)
        b_hi_idx = torch.empty(pairs_per_row, dtype=torch.long)
        for p in range(pairs_per_row):
            idx = IND[i][p]
            if idx not in INDEX_TO_PAIR:
                raise ValueError(f"Invalid index {idx} at row {i}, pair {p}")
            pos_lo, pos_hi = INDEX_TO_PAIR[idx]
            b_lo_idx[p] = 4 * p + pos_lo
            b_hi_idx[p] = 4 * p + pos_hi

        a_lo = A[i, 0::2]                # (pairs_per_row,) bf16
        a_hi = A[i, 1::2]                # (pairs_per_row,) bf16
        b_lo = B[b_lo_idx]               # (pairs_per_row,) bf16
        b_hi = B[b_hi_idx]               # (pairs_per_row,) bf16

        prod_lo  = bf16_mul(a_lo, b_lo)
        prod_hi  = bf16_mul(a_hi, b_hi)
        pair_sum = bf16_add(prod_lo, prod_hi)        # one bf16 add per pair

        acc = torch.zeros((), dtype=torch.bfloat16)
        for p in range(pairs_per_row):
            acc = bf16_add(acc, pair_sum[p])         # sequential bf16 accumulation

        y[i] = acc

    with OUTPUT_FILE.open("w") as f:
        for i in range(M):
            f.write(bf16_scalar_to_hex(y[i]) + "\n")

    print(f"Wrote {M} bfloat16 results to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
