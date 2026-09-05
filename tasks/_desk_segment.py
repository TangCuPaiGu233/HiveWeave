"""Segment desk-set into partition / monitors / desktop for a new back/front recut."""
from pathlib import Path
from PIL import Image
import numpy as np
from collections import deque

ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
OUT = Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug")

full = np.array(Image.open(ASSETS / "office-desk-set.png").convert("RGBA"))
H, W = full.shape[:2]
a = full[:, :, 3] > 8
rgb = full[:, :, :3].astype(np.int16)

def flood(seeds, pred, maxn=200000):
    vis = np.zeros((H, W), dtype=bool)
    q = deque()
    for s in seeds:
        q.append(s)
    n = 0
    while q and n < maxn:
        y, x = q.popleft()
        if y < 0 or y >= H or x < 0 or x >= W or vis[y, x]:
            continue
        if not pred(y, x):
            continue
        vis[y, x] = True
        n += 1
        q.append((y - 1, x)); q.append((y + 1, x))
        q.append((y, x - 1)); q.append((y, x + 1))
    return vis

# --- partition: isolated at the very top (row 0-8, x ~109-116) ---
top_part_seeds = [(y, x) for y in range(0, 12) for x in range(100, 125) if a[y, x]]
print("partition seeds", len(top_part_seeds))

def is_partition(y, x):
    if not a[y, x]:
        return False
    r, g, b = rgb[y, x]
    # white / light grey panel (not green plant, not blue screen, not black chair)
    if b > r + 25 and b > 90:  # blue monitor
        return False
    if g > r + 20 and g > 70:  # plant
        return False
    if r < 70 and g < 70 and b < 70:  # dark chair / bezel
        return False
    # panel is light
    return int(r) + int(g) + int(b) > 380 and abs(int(r) - int(g)) < 40

part = flood(top_part_seeds, is_partition)
print("partition px", int(part.sum()), "bbox", 
      (int(np.where(part)[1].min()), int(np.where(part)[0].min()),
       int(np.where(part)[1].max()), int(np.where(part)[0].max())) if part.any() else None)

# --- blue monitors ---
blue = a & (rgb[:, :, 2] > 80) & (rgb[:, :, 2] > rgb[:, :, 0] + 15) & (rgb[:, :, 2] > rgb[:, :, 1])
print("blue px", int(blue.sum()))
# dilate to bezels
from numpy import pad
ker = np.ones((7, 7), dtype=bool)
# simple dilate
def dilate(m, r=3):
    out = m.copy()
    ys, xs = np.where(m)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            yy = ys + dy
            xx = xs + dx
            ok = (yy >= 0) & (yy < H) & (xx >= 0) & (xx < W)
            out[yy[ok], xx[ok]] = True
    return out

mon = dilate(blue, 4) & a
# restrict bezel to dark-ish or already-blue (don't eat the white desk)
r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
darkish = (r < 140) & (g < 140) & (b < 180)
mon = (blue | (mon & darkish)) & a
print("monitor px", int(mon.sum()), "bbox",
      (int(np.where(mon)[1].min()), int(np.where(mon)[0].min()),
       int(np.where(mon)[1].max()), int(np.where(mon)[0].max())) if mon.any() else None)

# --- left black monitor: dark rectangle left of partition, upper-middle ---
# Look at columns 70-105, rows 20-70 for a dark rectangular screen
left_dark = a & (r < 90) & (g < 90) & (b < 90)
# connected components-ish: seed from dark pixels near partition left, mid height
left_seeds = [(y, x) for y in range(15, 70) for x in range(70, 110)
              if left_dark[y, x] and not part[y, x]]
print("left dark seeds", len(left_seeds))

def is_left_monitor(y, x):
    if not a[y, x] or part[y, x]:
        return False
    rr, gg, bb = rgb[y, x]
    # dark screen / bezel, not chair (chair is lower and more left)
    if y > 95:
        return False
    if x < 55:
        return False
    return rr < 110 and gg < 110 and bb < 120

left_mon = flood(left_seeds[:50], is_left_monitor) if left_seeds else np.zeros((H, W), bool)
print("left monitor px", int(left_mon.sum()))

tall = part | mon | left_mon
print("tall occluders", int(tall.sum()))

# Visualize
vis = np.zeros((H, W, 4), dtype=np.uint8)
vis[part] = (255, 220, 40, 255)       # yellow partition
vis[mon] = (40, 90, 255, 255)         # blue monitors
vis[left_mon] = (180, 40, 255, 255)   # purple left monitor
# show original faintly
base = full.copy()
base[~a, 3] = 0
comp = Image.alpha_composite(Image.fromarray(base), Image.fromarray(vis))
comp.save(OUT / "seg_tall.png")

# Save masks
Image.fromarray((tall * 255).astype(np.uint8)).save(OUT / "mask_tall.png")
Image.fromarray((part * 255).astype(np.uint8)).save(OUT / "mask_part.png")
print("wrote seg masks")
