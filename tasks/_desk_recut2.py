"""Conservative recut: move only monitor+partition (x=90..130, y=0..82) to BACK."""
from pathlib import Path
from PIL import Image
import numpy as np

ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
OUT = Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug")
SCALE = 208 / 159.2
SPLIT = 54

full = np.array(Image.open(ASSETS / "office-desk-set.png").convert("RGBA"))
cur_back = np.array(Image.open(ASSETS / "office-desk-back.png").convert("RGBA"))
cur_front = np.array(Image.open(ASSETS / "office-desk-front.png").convert("RGBA"))
H, W = full.shape[:2]
assert cur_front.shape[:2] == (H - SPLIT, W), (cur_front.shape, H, W)
# back is 201x54; paste at x=3 (208-201=7, empirically ~3 from earlier leftTop delta)
BACK_XOFF = W - cur_back.shape[1]  # 7? let's check
print("full", full.shape, "back", cur_back.shape, "front", cur_front.shape, "xoff guess", BACK_XOFF)

r = full[:, :, 0].astype(np.int16)
g = full[:, :, 1].astype(np.int16)
b = full[:, :, 2].astype(np.int16)
a = full[:, :, 3] > 8

blue = a & (b > 90) & (b > r + 10)
# tight ROI for the center stack (panel + both screens)
roi = np.zeros((H, W), dtype=bool)
roi[0:84, 88:132] = True

def dilate(m, rad=3):
    out = m.copy()
    ys, xs = np.where(m)
    for dy in range(-rad, rad + 1):
        for dx in range(-rad, rad + 1):
            yy, xx = ys + dy, xs + dx
            ok = (yy >= 0) & (yy < H) & (xx >= 0) & (xx < W)
            out[yy[ok], xx[ok]] = True
    return out

mon = dilate(blue & roi, 4) & a & roi
# white/light vertical panel inside ROI, only where column is "narrow-ish"
light = a & (r > 170) & (g > 170) & (b > 165)
# keep light pixels in the panel band x=94-110 (left of the blue monitor)
panel = light & roi & (np.arange(W)[None, :] < 112) & (np.arange(W)[None, :] > 92)
# don't take wide desktop: in rows >= 70, require the pixel not be part of a long white run
wide_desk = np.zeros((H, W), dtype=bool)
for y in range(70, H):
    row_light = light[y]
    # runs
    i = 0
    while i < W:
        if not row_light[i]:
            i += 1
            continue
        j = i
        while j < W and row_light[j]:
            j += 1
        if j - i >= 24:
            wide_desk[y, i:j] = True
        i = j

tall = (mon | panel) & ~wide_desk
print("tall", int(tall.sum()), "front-part", int(tall[SPLIT:].sum()), "back-part", int(tall[:SPLIT].sum()))

# New BACK: place current back into 208x170, then stamp tall from full
new_back = np.zeros_like(full)
# Try x offsets 0,3,7 to match current back
# Current back content starts at x=94 in its own image.
# We'll paste at x=7 (208-201) first and also stamp tall which is in full coords.
xoff = 7
new_back[: cur_back.shape[0], xoff : xoff + cur_back.shape[1]] = cur_back
new_back[tall] = full[tall]

# New FRONT: current front minus tall
new_front = cur_front.copy()
new_front[tall[SPLIT:]] = 0

# Crop back to opaque bbox (keep full width aligned to world leftTop of current front)
# We'll save full 208 x needed-height so leftTop can stay aligned with front's x
bb_y = np.where(new_back[:, :, 3] > 8)[0]
back_h = int(bb_y.max()) + 1 if len(bb_y) else SPLIT
new_back_crop = new_back[:back_h]
print("new back size", new_back_crop.shape, "front", new_front.shape)

Image.fromarray(new_back_crop).save(OUT / "new_back.png")
Image.fromarray(new_front).save(OUT / "new_front.png")

vis = full.copy()
vis[tall] = (np.array([255, 50, 50, 255]) * 0.55 + vis[tall] * 0.45).astype(np.uint8)
Image.fromarray(vis).save(OUT / "tall_overlay.png")

# Composite
purple = Image.open(ASSETS / "agent-purple-typing-sheet.png").convert("RGBA")
frame0 = purple.crop((0, 0, 96, 96))
disp = int(round(96 * 0.8 * SCALE))
char = frame0.resize((disp, disp), Image.Resampling.NEAREST)

def world_to_full(wx, wy):
    s = 159.2 / 208
    return (wx - (-78.2)) / s, (wy - (-62.1)) / s

def compose(dx, dy, name):
    cx, cy = world_to_full(dx, dy)
    paste = (int(round(cx - 0.5 * disp)), int(round(cy - 0.875 * disp)))
    comp = Image.new("RGBA", (208, 170), (36, 32, 30, 255))
    comp.paste(Image.fromarray(new_back_crop), (0, 0), Image.fromarray(new_back_crop))
    comp.paste(char, paste, char)
    comp.paste(Image.fromarray(new_front), (0, SPLIT), Image.fromarray(new_front))
    comp.save(OUT / name)
    print(name, "paste", paste)

compose(-40, 8, "preview_recut_dx40_dy8.png")
compose(-40, 18, "preview_recut_dx40_dy18.png")
print("done")
