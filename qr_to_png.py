import sys
import os
try:
    from PIL import Image
except ImportError:
    print("Error: The 'Pillow' library is required. Install it using: pip install Pillow")
    sys.exit(1)

def convert_qr_ascii_to_png(input_file, output_file, scale=10):
    """
    Converts an ASCII QR code (using █, ▀, ▄ blocks) into a PNG image.
    Each character in the ASCII representation typically represents TWO vertical pixels.
    """
    if not os.path.exists(input_file):
        print(f"Error: File '{input_file}' not found.")
        return

    with open(input_file, 'r', encoding='utf-8') as f:
        # Filter lines to remove empty ones at start/end but keep content structure
        lines = [line.rstrip('\r\n') for line in f]
    
    if not lines:
        print("Error: Input file is empty.")
        return

    qr_matrix = []
    
    for line in lines:
        # Each ASCII line contains TWO vertical rows of the QR code
        row_top = []
        row_bottom = []
        
        for char in line:
            # Mapping based on how qrcode library generates ASCII:
            # ' ' (space) -> Both pixels white
            # '█' (full block) -> Both pixels black
            # '▀' (upper half) -> Top black, bottom white
            # '▄' (lower half) -> Top white, bottom black
            
            if char == '█':
                row_top.append(1)
                row_bottom.append(1)
            elif char == '▀':
                row_top.append(1)
                row_bottom.append(0)
            elif char == '▄':
                row_top.append(0)
                row_bottom.append(1)
            elif char in (' ', '\xa0'): # Handle normal and non-breaking spaces
                row_top.append(0)
                row_bottom.append(0)
            else:
                # Ignore or treat unknown as space
                row_top.append(0)
                row_bottom.append(0)
        
        if row_top:
            qr_matrix.append(row_top)
            qr_matrix.append(row_bottom)

    if not qr_matrix:
        print("Error: No QR content detected in the file.")
        return

    # Normalize widths (in case of trailing spaces missing in the text file)
    width = max(len(row) for row in qr_matrix)
    height = len(qr_matrix)
    
    for row in qr_matrix:
        if len(row) < width:
            row.extend([0] * (width - len(row)))

    # Create a 1-bit image (0 = Black, 1 = White in PIL '1' mode)
    # Our matrix has 1 for Black, 0 for White, so we invert it.
    img = Image.new('1', (width, height), color=1)
    pixels = img.load()

    for y, row in enumerate(qr_matrix):
        for x, val in enumerate(row):
            pixels[x, y] = 0 if val else 1

    # Apply scaling for visibility (nearest neighbor to keep blocks sharp)
    if scale > 1:
        img = img.resize((width * scale, height * scale), resample=Image.NEAREST)

    img.save(output_file)
    print(f"Successfully converted '{input_file}' to '{output_file}' ({width}x{height} pixels).")

if __name__ == "__main__":
    # Default values
    input_path = "login_qr.txt"
    output_path = "login_qr.png"
    
    if len(sys.argv) > 1:
        input_path = sys.argv[1]
    if len(sys.argv) > 2:
        output_path = sys.argv[2]
        
    convert_qr_ascii_to_png(input_path, output_path)
