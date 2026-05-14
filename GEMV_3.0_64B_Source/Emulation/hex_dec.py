import torch

# Variables you can easily change
input_filename = "C_128x1.txt"
output_filename = "C_128x1_decimal.txt"

def process_bfloat16_file(input_file, output_file):
    with open(input_file, 'r') as infile, open(output_file, 'w') as outfile:
        for line in infile:
            # Check if the line ends with a newline character so we can preserve it
            has_newline = line.endswith('\n')
            
            # Remove any whitespace or newline characters from the edges for processing
            clean_line = line.strip()
            
            # If the line was completely empty or just a newline, preserve the newline and move on
            if not clean_line:
                if has_newline:
                    outfile.write('\n')
                continue
                
            # Ensure the line length is perfectly divisible by 4
            if len(clean_line) % 4 != 0:
                print(f"Warning: Skipping malformed line (length not multiple of 4): {clean_line}")
                continue
                
            # 1. Break the contiguous string into 4-character chunks and parse as integers
            int_vals = [int(clean_line[i:i+4], 16) for i in range(0, len(clean_line), 4)]
            
            # 2. Use PyTorch to reinterpret the raw bits:
            # We load as int32, downcast to int16 (which safely forces the bit patterns without overflowing),
            # and then view those exact bits as bfloat16 memory.
            bf16_tensor = torch.tensor(int_vals, dtype=torch.int32).to(torch.int16).view(torch.bfloat16)
            
            # 3. Format each element to 2 decimal places
            formatted_floats = [f"{val.item():.2f}" for val in bf16_tensor]
            
            # 4. Join them with a space and write them out
            outfile.write(" ".join(formatted_floats))
            
            # 5. Restore the newline character if it existed in the original line
            if has_newline:
                outfile.write('\n')

    print(f"Processing complete! Output written to {output_file}")

# Execute the function
if __name__ == "__main__":
    process_bfloat16_file(input_filename, output_filename)