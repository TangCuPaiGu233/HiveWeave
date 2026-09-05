"""Sample colors along the partition column and around the split."""
from pathlib import Path
from PIL import Image
import numpy as np

full = np.array(Image.open(Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets\office-desk-set.png")).convert("RGBA"))
H, W = full.shape[:2]

print("=== column 110 (partition center guess) RGB every 4 rows ===")
for y in range(0, H, 4):
    r, g, b, a = full[y, 110]
    if a > 8:
        print(f"  y={y:3d} rgba=({r:3d},{g:3d},{b:3d},{a:3d})")

print("\n=== row 20 x=90..130 ===")
for x in range(90, 130, 2):
    r, g, b, a = full[20, x]
    if a > 8:
        print(f"  x={x:3d} rgba=({r:3d},{g:3d},{b:3d},{a:3d})")

print("\n=== row 54 (split) x=0..207 step 8 ===")
for x in range(0, W, 8):
    r, g, b, a = full[54, x]
    print(f"  x={x:3d} rgba=({r:3d},{g:3d},{b:3d},{a:3d})")

print("\n=== row 40 (back lower) x=40..80 character region ===")
for x in range(30, 90, 4):
    r, g, b, a = full[40, x]
    print(f"  x={x:3d} rgba=({r:3d},{g:3d},{b:3d},{a:3d})")
