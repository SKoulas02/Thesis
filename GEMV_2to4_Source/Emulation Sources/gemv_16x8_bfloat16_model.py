import os
import torch
from pathlib import Path

# --- Configuration ---
# Matrix A: 16x8 (Compressed). 16 rows, 8 non-zero elements per row.
# Vector B: 16 elements (Dense).
# Sparsity: 2:4 structured sparsity.
# Each 8-element row of A is logically 16 elements. 
# We split the 16-element B vector into 4 chunks of 4.
# For each chunk, 2 elements are selected via the index to multiply with the 2 A elements.
# Result is a standard 16-element output vector Y.

MATRIX_ROWS = 16
MATRIX_COLS_COMPRESSED = 8
VECTOR_SIZE = 16
CHUNKS_PER_ROW = VECTOR_SIZE // 4

def create_dummy_files(data_dir):
    """Creates sample files for a 16x8 compressed matrix and 16-element vector."""
    os.makedirs(data_dir, exist_ok=True)
    
    a_path = os.path.join(data_dir, "A.txt")
    if not os.path.exists(a_path):
        with open(a_path, "w") as f:
            # 16 rows, 8 elements per row (128 total elements)
            for _ in range(MATRIX_ROWS):
                f.write("2.0 2.0 2.0 2.0 2.0 2.0 2.0 2.0\n")
                
    b_path = os.path.join(data_dir, "B.txt")
    if not os.path.exists(b_path):
        with open(b_path, "w") as f:
            # 16 dense elements
            f.write("1.0 2.0 2.0 1.0 1.0 2.0 2.0 1.0 1.0 2.0 2.0 1.0 1.0 2.0 2.0 1.0\n")
            
    idx_path = os.path.join(data_dir, "indices.txt")
    if not os.path.exists(idx_path):
        with open(idx_path, "w") as f:
            # 16 rows, 4 indices per row (64 total indices)
            for _ in range(MATRIX_ROWS):
                f.write("011 101 011 101\n")

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
    data_dir = "gemv_16x8_data"
    create_dummy_files(data_dir)
    
    a_vals = parse_file(os.path.join(data_dir, "A.txt"), is_index=False)
    b_vals = parse_file(os.path.join(data_dir, "B.txt"), is_index=False)
    idx_vals = parse_file(os.path.join(data_dir, "indices.txt"), is_index=True)
    
    if not a_vals or not b_vals or not idx_vals:
        print("Missing data in input files. Exiting.")
        return

    index_map = {
        0: (0, 1), # "000"
        1: (0, 2), # "001"
        2: (0, 3), # "010"
        3: (1, 2), # "011"
        4: (1, 3), # "100"
        5: (2, 3)  # "101"
    }
    
    # Create the single 16-element B vector
    vector_b = torch.tensor(b_vals[:VECTOR_SIZE], dtype=torch.bfloat16)

    output_y = []
    output_y_tensors = []

    output_file = os.path.join(data_dir, "output_16x8.txt")
    with open(output_file, "w") as f_out:
        f_out.write("--- GEMV 16x8 Compressed (16x16 Dense) bfloat16 Hardware Model Output ---\n\n")
        
        a_cursor = 0
        idx_cursor = 0
        
        # Calculate row by row
        for row in range(MATRIX_ROWS):
            f_out.write(f"--- Row {row} ---\n")
            row_sum = torch.tensor(0.0, dtype=torch.bfloat16)
            
            # Process the 4 chunks in the row
            for chunk in range(CHUNKS_PER_ROW):
                a_chunk = torch.tensor(a_vals[a_cursor : a_cursor+2], dtype=torch.bfloat16)
                b_chunk = vector_b[chunk*4 : chunk*4+4]
                idx = idx_vals[idx_cursor]
                
                if idx not in index_map:
                    f_out.write(f"  Chunk {chunk}: Invalid index {idx}, skipping.\n")
                    continue
                    
                sel_b0, sel_b1 = index_map[idx]
                b_selected = torch.stack([b_chunk[sel_b0], b_chunk[sel_b1]]).to(torch.bfloat16)
                
                mult_res = (a_chunk * b_selected).to(torch.bfloat16)
                add_res = torch.sum(mult_res).to(torch.bfloat16)
                
                row_sum += add_res
                row_sum = row_sum.to(torch.bfloat16)
                
                out_str = (
                    f"  Chunk {chunk}:\n"
                    f"    A Input             : [{a_chunk[0].item():.4f}, {a_chunk[1].item():.4f}]\n"
                    f"    B Input             : [{b_chunk[0].item():.4f}, {b_chunk[1].item():.4f}, {b_chunk[2].item():.4f}, {b_chunk[3].item():.4f}]\n"
                    f"    Index               : {idx} -> Selected B: [{b_selected[0].item():.4f}, {b_selected[1].item():.4f}]\n"
                    f"    Multiplier out      : [{mult_res[0].item():.4f}, {mult_res[1].item():.4f}] (bfloat16)\n"
                    f"    Adder/Accum out     : {add_res.item():.4f} (bfloat16)\n"
                )
                f_out.write(out_str)
                
                a_cursor += 2
                idx_cursor += 1
                
            output_y.append(row_sum.item())
            output_y_tensors.append(row_sum)
            f_out.write(f"  => Row {row} Final Accumulation: {row_sum.item():.4f} (bfloat16)\n")
            f_out.write("----------------------------------------\n")
            
    print(f"Calculation complete. Check '{output_file}' for the detailed pipeline breakdown.")
    print("\n--- Final Output Vector Y (16 elements) ---")
    for i, val in enumerate(output_y):
        print(f"Y[{i}] = {val:.4f}")

    # Write the output as a clean hex file for Vivado Testbench verification
    output_hex_file = os.path.join(data_dir, "output_16x8_hex.txt")
    with open(output_hex_file, "w") as f_hex:
        for tensor_val in output_y_tensors:
            # Interpret the raw 16-bits of the bfloat16 as an int, then format as hex
            raw_bits = tensor_val.view(torch.short).item() & 0xFFFF
            f_hex.write(f"{raw_bits:04X}\n")
    print(f"\nHex output saved to '{output_hex_file}'.")

if __name__ == "__main__":
    main()