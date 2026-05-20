import torch


def hex_to_bf16(hex_str: str) -> torch.Tensor:
    bits = int(hex_str, 16) & 0xFFFF
    raw = torch.tensor([bits], dtype=torch.int16)
    return raw.view(torch.bfloat16)


def bf16_to_hex(value: torch.Tensor) -> str:
    raw_bits = value.view(torch.int16).item() & 0xFFFF
    return f"{raw_bits:04X}"


def main():
    print("BF16 Add/Multiply Calculator (enter hex values, e.g. 3F80)")
    print("Type 'q' or 'Q' to exit.\n")

    while True:
        a_input = input("Enter A (hex): ").strip()
        if a_input.lower() == 'q':
            break

        b_input = input("Enter B (hex): ").strip()
        if b_input.lower() == 'q':
            break

        try:
            a_bf16 = hex_to_bf16(a_input)
            b_bf16 = hex_to_bf16(b_input)
        except ValueError:
            print("Invalid hex value. Try again.\n")
            continue

        sum_bf16  = (a_bf16 + b_bf16).to(torch.bfloat16)
        prod_bf16 = (a_bf16 * b_bf16).to(torch.bfloat16)

        sum_hex  = bf16_to_hex(sum_bf16)
        prod_hex = bf16_to_hex(prod_bf16)

        print(f"A    = {a_input.upper()}  ({a_bf16.item()})")
        print(f"B    = {b_input.upper()}  ({b_bf16.item()})")
        print(f"A+B  = {sum_hex}  ({sum_bf16.item()})")
        print(f"A*B  = {prod_hex}  ({prod_bf16.item()})")
        print()


if __name__ == "__main__":
    main()
