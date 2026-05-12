#!/usr/bin/env python
"""Create test DNA files for local demo."""
import os

os.makedirs("control-plane/inputs", exist_ok=True)

# Create identical size sequences (200 bytes each)
seq_a = b"ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT"  # 100 bytes
seq_a = seq_a + seq_a  # Double it to 200 bytes

# Same content but with intentional differences at positions 50 and 150
seq_b = bytearray(seq_a)
seq_b[50] = ord('T') if seq_b[50] == ord('A') else ord('A')
seq_b[150] = ord('G') if seq_b[150] == ord('C') else ord('C')
seq_b = bytes(seq_b)

# Write files
with open("control-plane/inputs/A.clean", "wb") as f:
    f.write(seq_a)

with open("control-plane/inputs/B.clean", "wb") as f:
    f.write(seq_b)

print("Created test DNA files:")
print(f"  A.clean size: {os.path.getsize('control-plane/inputs/A.clean')} bytes")
print(f"  B.clean size: {os.path.getsize('control-plane/inputs/B.clean')} bytes")
