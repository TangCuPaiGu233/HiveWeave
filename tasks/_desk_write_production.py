"""Write production desk sprites: iso-edge FRONT + full monitor in BACK. 1px seam overlap."""
from pathlib import Path
from PIL import Image
import numpy as np
import shutil

ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
SPLIT = 54
PX = 208 / 159.2
FRONT_LT = (-78.2, -20.8)
BACK_LT = (-78.2, -62.1)

full = np.array(Image.open(ASSETS / "office-desk-set.png").convert("RGBA"))
cur_back = np.array(Image.open(ASSETS / "office-desk-back.png").convert("RGBA"))
H, W = full.shape[:2]
a = full[:, :, 3] > 8
r = full[:, :, 0].astype(np.int16)
g = full[:, :, 1].astype(np.int16)
bch = full[:, :, 2].astype(np.int16)

roi = np.zeros((H, W), dtype=bool)
roi[0:82, 88:126] = True
blue = a & roi & (bch > 90) & (bch > r + 8)

def dilate(m, rad):
    out = np.zeros_like(m)
    ys, xs = np.where(m)
    for dy in range(-rad, rad + 1):
        for dx in range(-rad, rad + 1):
            yy, xx = ys + dy, xs + dx
            ok = (yy >= 0) & (yy < H) & (xx >= 0) & (xx < W)
            out[yy[ok], xx[ok]] = True
    return out

monitor = dilate(blue, 3) & a & roi

new_front = full[SPLIT:].copy()
wedge = np.zeros((H, W), dtype=bool)
for x in range(W):
    rel_x = FRONT_LT[0] + x / PX
    far_y = -20.8 + 0.5 * abs(rel_x)
    far_row_full = (far_y - BACK_LT[1]) * PX
    # leave 1px overlap with FRONT so the iso-edge doesn't show a floor gap
    cut = int(np.floor(far_row_full - SPLIT)) - 1
    if cut > 0:
        new_front[:cut, x] = 0
        wedge[SPLIT : SPLIT + cut + 1, x] = True  # +1 overlap row
new_front[monitor[SPLIT:]] = 0

new_back = np.zeros_like(full)
new_back[:SPLIT, 3 : 3 + cur_back.shape[1]] = cur_back
new_back[monitor] = full[monitor]
wedge &= a
wedge &= ~monitor
new_back[wedge] = full[wedge]

# harden seam alpha (skill: half-alpha at the cut shows floor as an orange line)
for img in (new_back, new_front):
    alpha = img[:, :, 3]
    img[:, :, 3] = np.where((alpha > 0) & (alpha < 255), 255, alpha)

ys = np.where(new_back[:, :, 3] > 8)[0]
back_h = int(ys.max()) + 1
new_back = new_back[:back_h]

# backup once
for name in ("office-desk-back.png", "office-desk-front.png"):
    src = ASSETS / name
    bak = ASSETS / name.replace(".png", ".pre-iso.bak.png")
    if src.exists() and not bak.exists():
        shutil.copy2(src, bak)
        print("backed up", bak.name)

Image.fromarray(new_back).save(ASSETS / "office-desk-back.png")
Image.fromarray(new_front).save(ASSETS / "office-desk-front.png")
print("wrote back", new_back.shape, "front", new_front.shape)
print("DESK_SET.back = { w: 159.2, h:", round(back_h / PX, 1), ", leftTop: { x: -78.2, y: -62.1 } }")
