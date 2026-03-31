import struct

def float32_to_bfloat16_hex(f):
    """
    Converts a float32 to a bfloat16 hex string.
    bfloat16 is essentially the top 16 bits of a float32.
    """
    # Pack float into 4 bytes (IEEE 754 float32)
    [i] = struct.unpack('>I', struct.pack('>f', f))
    
    # Rounding to nearest even (optional but recommended for accuracy)
    # This adds the bit at position 15 to the upper 16 bits
    i += (i >> 16) & 1
    i += 0x7FFF
    
    # Extract the most significant 16 bits
    bfloat16_bits = (i >> 16) & 0xFFFF
    
    # Return as 4-character hex (e.g., 0x4000)
    return f"{bfloat16_bits:04x}"

def convert_file_to_hex(input_file, output_file):
    try:
        with open(input_file, 'r') as infile, open(output_file, 'w') as outfile:
            for line in infile:
                if not line.strip():
                    continue
                
                # Split line into individual numbers
                numbers = line.split()
                
                # Convert each number to bfloat16 hex
                hex_values = [float32_to_bfloat16_hex(float(n)) for n in numbers]
                
                # Write to file with same spacing
                outfile.write(" ".join(hex_values) + "\n")
        
        print(f"Success: Hex output written to {output_file}")
        
    except FileNotFoundError:
        print("Error: A.txt not found.")

if __name__ == "__main__":
    convert_file_to_hex('B.txt', 'B_hex.txt')