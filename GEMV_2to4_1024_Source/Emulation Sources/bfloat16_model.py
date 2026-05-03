import os
import torch

def create_dummy_files():
    """Creates sample files to match the 2to4_TB.vhd stimulus if they don't exist."""
    if not os.path.exists("A.txt"):
        with open("A.txt", "w") as f:
            f.write("2.0 2.0\n2.0 2.0\n")
    if not os.path.exists("B.txt"):
        with open("B.txt", "w") as f:
            f.write("1.0 2.0 2.0 1.0\n1.0 2.0 2.0 1.0\n")
    if not os.path.exists("indices.txt"):
        with open("indices.txt", "w") as f:
            f.write("011\n101\n")

def parse_file(filename, is_index=False):
    """Reads whitespace-separated tokens from a file."""
    try:
        with open(filename, 'r') as f:
            tokens = f.read().split()
            
            if is_index:
                # Handle both binary string inputs ("011") and integer inputs ("3")
                return [int(t, 2) if set(t).issubset({'0', '1'}) else int(t) for t in tokens]
            else:
                # Parse standard floating point strings
                return [float(t) for t in tokens]
    except FileNotFoundError:
        print(f"Error: {filename} not found.")
        return []

def main():
    # 1. Ensure input files exist (generate mocks if missing)
    create_dummy_files()
    
    # 2. Read inputs
    a_vals = parse_file("A.txt", is_index=False)
    b_vals = parse_file("B.txt", is_index=False)
    idx_vals = parse_file("indices.txt", is_index=True)
    
    if not a_vals or not b_vals or not idx_vals:
        print("Missing data in input files. Exiting.")
        return

    # Hardware index mapping matching c_block.vhd:
    # Index (bin) -> B Selection (Mult1_B, Mult2_B)
    index_map = {
        0: (0, 1), # "000"
        1: (0, 2), # "001"
        2: (0, 3), # "010"
        3: (1, 2), # "011"
        4: (1, 3), # "100"
        5: (2, 3)  # "101"
    }

    # Determine how many 2:4 sparse blocks we can process
    num_blocks = min(len(a_vals) // 2, len(b_vals) // 4, len(idx_vals))
    
    with open("output.txt", "w") as f_out:
        f_out.write("--- GEMV 2:4 bfloat16 Hardware Model Output ---\n\n")
        
        for i in range(num_blocks):
            # Fetch chunks for this block
            # A is processed in chunks of 2, B in chunks of 4
            a_chunk = torch.tensor(a_vals[i*2 : i*2+2], dtype=torch.bfloat16)
            b_chunk = torch.tensor(b_vals[i*4 : i*4+4], dtype=torch.bfloat16)
            idx = idx_vals[i]

            if idx not in index_map:
                f_out.write(f"Block {i}: Invalid index {idx}, skipping.\n")
                continue
                
            # Map the index to the selected B elements
            sel_b0, sel_b1 = index_map[idx]
            
            # Explicitly cast selections to bfloat16
            b_selected = torch.stack([b_chunk[sel_b0], b_chunk[sel_b1]]).to(torch.bfloat16)

            # --- MIDDLE STAGE CALCULATIONS ---
            
            # 1. Multiplier Stage
            mult_res = a_chunk * b_selected
            # Force intermediate result to remain in bfloat16
            mult_res = mult_res.to(torch.bfloat16)
            
            # 2. Adder Stage
            add_res = torch.sum(mult_res)
            # Force final result to remain in bfloat16
            add_res = add_res.to(torch.bfloat16)

            # --- FORMATTED OUTPUT ---
            # Formatting outputs for easy debugging against the VHDL Testbench
            out_str = (
                f"Block {i}:\n"
                f"  A Input             : [{a_chunk[0].item():.4f}, {a_chunk[1].item():.4f}]\n"
                f"  B Input             : [{b_chunk[0].item():.4f}, {b_chunk[1].item():.4f}, {b_chunk[2].item():.4f}, {b_chunk[3].item():.4f}]\n"
                f"  Index               : {idx} -> Selected B: [{b_selected[0].item():.4f}, {b_selected[1].item():.4f}]\n"
                f"  Multiplier 1 out    : {mult_res[0].item():.4f} (bfloat16)\n"
                f"  Multiplier 2 out    : {mult_res[1].item():.4f} (bfloat16)\n"
                f"  Adder/Accum out     : {add_res.item():.4f} (bfloat16)\n"
                "----------------------------------------\n"
            )
            
            print(out_str, end="")
            f_out.write(out_str)
            
    print("Calculation complete. Check 'output.txt' for the detailed pipeline breakdown.")

if __name__ == "__main__":
    # PyTorch is required. If not installed, run: pip install torch
    main()