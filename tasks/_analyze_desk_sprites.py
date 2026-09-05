"""Analyze office desk PNG sprites: size, alpha, split line, partition location."""
from pathlib import Path
from PIL import Image
import numpy as np

ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
files = [
    "office-desk-set.png",
    "office-desk-front.png",
    "office-desk-back.png",
    "office-frontdesk-set.png",
    "office-scene-bg.png",
    "agent-purple-typing-sheet.png",
]

for name in files:
    p = ASSETS / name
    im = Image.open(p)
    arr = np.array(im)
    print(f"\n=== {name} ===")
    print(f"  mode={im.mode} size={im.size} arr={arr.shape}")
    if arr.ndim == 3 and arr.shape[2] == 4:
        a = arr[:, :, 3]
        opaque = a > 0
        print(f"  alpha min/max/mean={a.min()}/{a.max()}/{a.mean():.1f}")
        print(f"  opaque px={int(opaque.sum())} ({100*opaque.mean():.1f}%)")
        rows = np.where(opaque.any(axis=1))[0]
        cols = np.where(opaque.any(axis=0))[0]
        if len(rows):
            print(f"  content bbox y={rows[0]}..{rows[-1]} x={cols[0]}..{cols[-1]}")
        # per-row opaque span (first/last 8 content rows)
        for yi in list(rows[:6]) + list(rows[-6:]):
            xs = np.where(opaque[yi])[0]
            semis = np.where((a[yi] > 0) & (a[yi] < 255))[0]
            print(f"    row {yi}: opaque x={xs[0]}..{xs[-1]} n={len(xs)} semi={len(semis)}")
    else:
        print(f"  no alpha, bands={arr.shape[2] if arr.ndim==3 else 1}")

# Compare set vs front+back compositing
print("\n=== set vs front/back alignment ===")
full = np.array(Image.open(ASSETS / "office-desk-set.png").convert("RGBA"))
front = np.array(Image.open(ASSETS / "office-desk-front.png").convert("RGBA"))
back = np.array(Image.open(ASSETS / "office-desk-back.png").convert("RGBA"))
print(f"full {full.shape} front {front.shape} back {back.shape}")

# Find content bboxes
def bbox(a):
    m = a[:, :, 3] > 8
    ys, xs = np.where(m)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())

print("full bbox", bbox(full))
print("front bbox", bbox(front))
print("back bbox", bbox(back))

# World geometry from constants
SCALE = 1.30625  # px = world * scale
back_w, back_h = 153.9 * SCALE, 41.3 * SCALE
front_w, front_h = 159.2 * SCALE, 88.8 * SCALE
print(f"expected back px {back_w:.1f}x{back_h:.1f}  front {front_w:.1f}x{front_h:.1f}")
print(f"actual   back px {back.shape[1]}x{back.shape[0]}  front {front.shape[1]}x{front.shape[0]}")

# Where would the split line be in the full image?
# back leftTop world (-75.9, -62.1), front leftTop (-78.2, -20.8)
# If we place them relative to a common origin (desk slot):
# back occupies y_world [-62.1, -62.1+41.3] = [-62.1, -20.8]
# front occupies y_world [-20.8, -20.8+88.8] = [-20.8, 68.0]
print("world back y [-62.1, -20.8)  front y [-20.8, 68.0]")
print("split at world y=-20.8  (= far desk edge)")
