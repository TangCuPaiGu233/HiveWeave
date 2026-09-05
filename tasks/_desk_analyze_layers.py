"""Dump layer occupancy of office-desk-set.png so we recut without guessing."""
from pathlib import Path
from PIL import Image, ImageDraw
import numpy as np

ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
OUT = Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug")
OUT.mkdir(exist_ok=True)

PX = 208 / 159.2
FRONT_LT = (-78.2, -20.8)
BACK_LT = (-78.2, -62.1)
SPLIT = 54

full = np.array(Image.open(ASSETS / "office-desk-set.png").convert("RGBA"))
H, W = full.shape[:2]
r, g, b, a = [full[:, :, i].astype(np.int16) for i in range(4)]
opaque = a > 8
white = opaque & (r > 180) & (g > 180) & (b > 180)
dark = opaque & (r < 85) & (g < 85) & (b < 95)
blue = opaque & (b > 90) & (b > r + 8)
green = opaque & (g > r + 15) & (g > b + 15) & (g > 80)
print("size", W, H)
print("opaque", int(opaque.sum()), "white", int(white.sum()),
      "dark", int(dark.sum()), "blue", int(blue.sum()), "green", int(green.sum()))

def bbox(m):
    ys, xs = np.where(m)
    if len(ys) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()), int(m.sum())

print("dark bbox", bbox(dark))
print("blue bbox", bbox(blue))
print("green bbox", bbox(green))

# per-x dark runs
print("--- dark columns (x, ymin, ymax, count) left half ---")
for x in range(0, 100, 4):
    col = dark[:, x]
    if col.any():
        ys = np.where(col)[0]
        print(f"  x={x:3d} y={ys.min():3d}..{ys.max():3d} n={len(ys)}")

print("--- blue columns ---")
for x in range(W):
    col = blue[:, x]
    if col.any():
        ys = np.where(col)[0]
        if x % 2 == 0:
            print(f"  x={x:3d} y={ys.min():3d}..{ys.max():3d} n={len(ys)}")

# iso edge overlay
vis = full.copy()
for x in range(W):
    rel_x = FRONT_LT[0] + x / PX
    far_y = -20.8 + 0.5 * abs(rel_x)
    far_row = int(round((far_y - BACK_LT[1]) * PX))
    if 0 <= far_row < H:
        vis[far_row, x] = (255, 0, 0, 255)
        if far_row + 1 < H:
            vis[far_row + 1, x] = (255, 80, 80, 255)
Image.fromarray(vis).save(OUT / "iso_edge_overlay.png")
print("wrote iso_edge_overlay.png")

# colorize: dark=red, blue=cyan, white=keep, else yellow
ov = full.copy()
ov[dark] = (220, 40, 40, 255)
ov[blue] = (40, 200, 255, 255)
Image.fromarray(ov).save(OUT / "color_classes.png")
print("wrote color_classes.png")
