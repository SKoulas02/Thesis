import torch

# --- Configuration ---
INPUT_A_HEX = "3FC0"   # bfloat16 in hex (e.g. 3F80 = 1.0)
INPUT_B_HEX = "3F9C"   # bfloat16 in hex (e.g. 4040 = 3.0)


def hex_to_bf16(hex_str: str) -> torch.Tensor:
    bits = int(hex_str, 16) & 0xFFFF
    raw = torch.tensor([bits], dtype=torch.int16)
    return raw.view(torch.bfloat16)


def bf16_to_hex(value: torch.Tensor) -> str:
    raw_bits = value.view(torch.int16).item() & 0xFFFF
    return f"{raw_bits:04X}"


def main():
    a_bf16 = hex_to_bf16(INPUT_A_HEX)
    b_bf16 = hex_to_bf16(INPUT_B_HEX)

    sum_bf16  = (a_bf16 + b_bf16).to(torch.bfloat16)
    prod_bf16 = (a_bf16 * b_bf16).to(torch.bfloat16)

    sum_hex  = bf16_to_hex(sum_bf16)
    prod_hex = bf16_to_hex(prod_bf16)

    print(f"A    = {INPUT_A_HEX}  ({a_bf16.item()})")
    print(f"B    = {INPUT_B_HEX}  ({b_bf16.item()})")
    print(f"A+B  = {sum_hex}  ({sum_bf16.item()})")
    print(f"A*B  = {prod_hex}  ({prod_bf16.item()})")


if __name__ == "__main__":
    main()
