"""Bit-faithful emulation of the 512B 2:4 sparse GEMV hardware (bfloat16).

Unlike gemv_2to4.py (a clean logical reference), this script reproduces the
*actual* datapath of two2four (Design/2to4_3.0_512B.vhd) and its submodules,
including the per-c_block accumulator rounding, the adder-tree rounding, and
the index distribution the hardware really performs. It matches the captured
hardware simulation output (Simulation/Output_2to4_512B.txt) bit-for-bit.

Inputs (logical, row-major; same files as gemv_2to4.py):
  A_FILE   : M x K_DENSE bfloat16 (4 hex chars each).  K_DENSE = K_LOGICAL/2.
  IND_FILE : M x (K_DENSE/2) 4-bit indices (binary, 4 chars each).
  B_FILE   : K_LOGICAL bfloat16 elements (4 hex chars), one per line.
Output:
  OUTPUT_FILE : M bfloat16 results (4 hex chars), one per line.

Datapath modelled (per c_block_3.0.vhd / adder_tree_1.0.vhd + IP configs):
  * Each pair: prod = bf16(A_even*B_sel_hi) , bf16(A_odd*B_sel_lo), summed with
    one Round-to-Nearest-Even bf16 add (Multiplier/Adder IPs).
  * Each output row is produced by NB=8 c_blocks; c_block j accumulates the two
    pairs {7-j, 15-j}. The Accumulator IP (custom bf16, internal fixed-point)
    sums its two inputs and emits bf16 by truncation (round-toward-zero).
  * The 8 c_block results feed a balanced bf16 adder tree (RN-even) -> y[i].

Index distribution (IMPORTANT - this reproduces a hardware quirk):
  In the 512B build the indices reorder uses CHUNK=8 while matrix_fifo packs
  64 bits (two rows) per indices FIFO, so each output row does NOT receive its
  own 16 indices. Instead the selector indices for output row `pos` come from
  the two source rows that landed in indices-FIFO `pos`, as reconstructed in
  selector_indices() below. (A and B are routed correctly; only the 2-of-4
  selectors are mis-sourced.) This is faithfully modelled so the emulation
  matches the hardware that is actually built.
"""

from pathlib import Path
import torch

# ---- Configuration ---------------------------------------------------------
A_FILE      = Path(__file__).parent / "A_64x32_SW.txt"
IND_FILE    = Path(__file__).parent / "Indices_64x16_SW.txt"
B_FILE      = Path(__file__).parent / "B_64x1_SW.txt"
OUTPUT_FILE = Path(__file__).parent / "C_64x1_HW_model.txt"

M           = 64     # matrix rows / output length
K_DENSE     = 32     # kept (non-zero) cols per row
K_LOGICAL   = 64     # logical x length (= 2 * K_DENSE)

HEX_CHARS   = 4      # hex chars per bfloat16 element
IND_CHARS   = 4      # bits per index element

# Structural constants of the hardware build (from the generics / reorder).
NB            = 8    # c_blocks per row (= BUS_EL / B_IDX); also pairs per read
ROWS_PER_LINE = 16   # indices rows packed per input beat (Indices_Reorder bus)
CHUNK         = 8    # index cols per row per beat (= NB)
# ---------------------------------------------------------------------------

PAIRS = K_DENSE // 2          # 16 pairs per row
ROW_BLOCKS = M // ROWS_PER_LINE  # 4


def hex_to_bf16_tensor(hex_chunks: list[str]) -> torch.Tensor:
    raw = torch.tensor([int(h, 16) & 0xFFFF for h in hex_chunks], dtype=torch.int16)
    return raw.view(torch.bfloat16)


def bf16_scalar_to_hex(v: torch.Tensor) -> str:
    return f"{v.view(torch.int16).item() & 0xFFFF:04X}"


def load_matrix_bf16(path: Path, rows: int, cols: int) -> torch.Tensor:
    lines = [ln.strip() for ln in path.open() if ln.strip()]
    if len(lines) != rows:
        raise ValueError(f"{path.name}: got {len(lines)} rows, expected {rows}")
    out = torch.empty((rows, cols), dtype=torch.bfloat16)
    for r, line in enumerate(lines):
        if len(line) != cols * HEX_CHARS:
            raise ValueError(f"{path.name}: line {r} width {len(line)}")
        out[r] = hex_to_bf16_tensor(
            [line[c * HEX_CHARS:(c + 1) * HEX_CHARS] for c in range(cols)])
    return out


def load_vector_bf16(path: Path, length: int) -> torch.Tensor:
    lines = [ln.strip() for ln in path.open() if ln.strip()]
    if len(lines) != length:
        raise ValueError(f"{path.name}: got {len(lines)} entries, expected {length}")
    return hex_to_bf16_tensor(lines)


def load_indices(path: Path, rows: int, cols: int) -> list[list[int]]:
    lines = [ln.strip() for ln in path.open() if ln.strip()]
    if len(lines) != rows:
        raise ValueError(f"{path.name}: got {len(lines)} rows, expected {rows}")
    return [[int(line[c * IND_CHARS:(c + 1) * IND_CHARS], 2) for c in range(cols)]
            for line in lines]


def bf16_rne(x: torch.Tensor) -> torch.Tensor:
    """bfloat16 with Round-to-Nearest-Even (Multiplier / Adder / tree IPs)."""
    return x.float().bfloat16()


def bf16_trunc(x: torch.Tensor) -> torch.Tensor:
    """fp32 -> bf16 by truncation / round-toward-zero (Accumulator IP output)."""
    raw = x.float().contiguous().view(torch.int32)
    return ((raw >> 16) & 0xFFFF).to(torch.int16).view(torch.bfloat16)


def selector_indices(pos: int, IND: list[list[int]]) -> list[int]:
    """The 16 per-pair 2-of-4 selectors the hardware actually feeds output row
    `pos`, accounting for the 512B indices-FIFO packing. Returns sel[P] for the
    B group 4P..4P+3 of row `pos`'s A pair P."""
    beat = pos // NB                 # which indices beat filled this FIFO
    j    = pos % NB                  # FIFO position within the beat
    col_block = beat // ROW_BLOCKS   # 0 -> index cols 0..7, 1 -> cols 8..15
    row_base  = ROWS_PER_LINE * (beat % ROW_BLOCKS)
    row_u = row_base + 2 * j         # MSB-half source row of the 64-bit FIFO word
    row_v = row_base + 2 * j + 1     # LSB-half source row
    sel = [0] * PAIRS
    for P in range(PAIRS):
        if P < NB:                   # first read (B groups 0..7 of row pos)
            src, src_pair = row_u, CHUNK * col_block + P
        else:                        # second read (B groups 8..15)
            src, src_pair = row_v, CHUNK * col_block + (P - NB)
        sel[P] = IND[src][src_pair]
    return sel


def main() -> None:
    if K_LOGICAL != 2 * K_DENSE:
        raise ValueError("K_LOGICAL must equal 2 * K_DENSE for 2:4 sparsity.")

    A   = load_matrix_bf16(A_FILE, M, K_DENSE)
    B   = load_vector_bf16(B_FILE, K_LOGICAL)
    IND = load_indices(IND_FILE, M, PAIRS)

    y = torch.empty(M, dtype=torch.bfloat16)

    for pos in range(M):
        sel = selector_indices(pos, IND)

        # ---- per-pair multiply + RN-even add (Multiplier/Adder IPs) --------
        pair_sum = [None] * PAIRS
        for P in range(PAIRS):
            v = sel[P]
            pos_hi = (v >> 2) & 0x3   # MSB 2 bits -> B for even-col A element
            pos_lo = v & 0x3          # LSB 2 bits -> B for odd-col  A element
            m_even = bf16_rne(A[pos, 2 * P].float()     * B[4 * P + pos_hi].float())
            m_odd  = bf16_rne(A[pos, 2 * P + 1].float() * B[4 * P + pos_lo].float())
            pair_sum[P] = bf16_rne(m_even.float() + m_odd.float())

        # ---- per-c_block accumulator: sum of pairs {7-j, 15-j}, truncate ---
        cblock = []
        for j in range(NB):
            s = pair_sum[(NB - 1) - j].double() + pair_sum[(2 * NB - 1) - j].double()
            cblock.append(bf16_trunc(s.float()))

        # ---- balanced bf16 adder tree (RN-even), order cb0..cb7 ------------
        level = cblock
        while len(level) > 1:
            level = [bf16_rne(level[k].float() + level[k + 1].float())
                     for k in range(0, len(level), 2)]
        y[pos] = level[0]

    with OUTPUT_FILE.open("w") as f:
        for pos in range(M):
            f.write(bf16_scalar_to_hex(y[pos]) + "\n")

    print(f"Wrote {M} bfloat16 results to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
