"""Recut desk so A-side sits IN the rear chair like the Kairosoft reference.

Layer rules (do NOT put the far-desktop wedge in BACK — that is why
people look like they sit on the tabletop):

  BACK  = rear chair (all) + pixels above the iso far-edge that are NOT
          near-side tall props (near monitor, plant, front chair)
  FRONT = desktop at/below iso-edge + front chair + near monitor + plant
          minus rear-chair pixels

Character is sandwiched: chair behind, desktop covering legs.
Seat: waist on far_edge(dx); dx centered on rear chair.
"""
from collections import deque
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps
import numpy as np
import shutil

ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
OUT = Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug")
OUT.mkdir(exist_ok=True)

PX = 208 / 159.2
FRONT_LT = (-78.2, -20.8)
BACK_LT = (-78.2, -62.1)
SPLIT = 54
WAIST_ABOVE_FOOT = 19.2  # 0.875*76.8 - 0.5*something; skill: 19.2 world

full = np.array(Image.open(ASSETS / "office-desk-set.png").convert("RGBA"))
H, W = full.shape[:2]
r = full[:, :, 0].astype(np.int16)
g = full[:, :, 1].astype(np.int16)
bch = full[:, :, 2].astype(np.int16)
a = full[:, :, 3]
opaque = a > 8
dark = opaque & (r < 85) & (g < 85) & (bch < 95)


def dilate(m, rad):
    out = m.copy()
    ys, xs = np.where(m)
    for dy in range(-rad, rad + 1):
        for dx in range(-rad, rad + 1):
            yy, xx = ys + dy, xs + dx
            ok = (yy >= 0) & (yy < H) & (xx >= 0) & (xx < W)
            out[yy[ok], xx[ok]] = True
    return out


def flood(seed_mask, pred, cap=40000):
    vis = np.zeros((H, W), dtype=bool)
    ys, xs = np.where(seed_mask)
    q = deque(zip(ys.tolist(), xs.tolist()))
    n = 0
    while q and n < cap:
        y, x = q.popleft()
        if y < 0 or y >= H or x < 0 or x >= W or vis[y, x] or not pred[y, x]:
            continue
        vis[y, x] = True
        n += 1
        q.extend((
            (y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1),
            (y - 1, x - 1), (y - 1, x + 1), (y + 1, x - 1), (y + 1, x + 1),
        ))
    return vis


def far_row_of(x):
    rel_x = FRONT_LT[0] + x / PX
    far_y = -20.8 + 0.5 * abs(rel_x)
    return (far_y - BACK_LT[1]) * PX


# Rear chair: dark blob on the left, seeded at the backrest
seed_rear = np.zeros((H, W), dtype=bool)
seed_rear[12:48, 18:72] = dark[12:48, 18:72]
rear_pred = dark.copy()
rear_pred[:, 96:] = False  # don't leak into front chair / near monitor
rear_chair = dilate(flood(seed_rear, rear_pred), 1) & opaque
print("rear chair", int(rear_chair.sum()),
      "bbox", tuple(int(v) for v in (
          np.where(rear_chair)[1].min(), np.where(rear_chair)[0].min(),
          np.where(rear_chair)[1].max(), np.where(rear_chair)[0].max())))

# Front chair: dark blob on the right, seeded at the backrest/seat
seed_front = np.zeros((H, W), dtype=bool)
seed_front[70:120, 130:190] = dark[70:120, 130:190]
front_pred = dark.copy()
front_pred[:, :110] = False
front_chair = dilate(flood(seed_front, front_pred), 1) & opaque
print("front chair", int(front_chair.sum()),
      "bbox", tuple(int(v) for v in (
          np.where(front_chair)[1].min(), np.where(front_chair)[0].min(),
          np.where(front_chair)[1].max(), np.where(front_chair)[0].max())))

# Near monitor (blue screen, the one that used to tear)
roi = np.zeros((H, W), dtype=bool)
roi[0:82, 88:126] = True
blue = opaque & roi & (bch > 90) & (bch > r + 8)
near_monitor = dilate(blue, 4) & opaque & roi
print("near monitor", int(near_monitor.sum()))

# Plant on the near desktop — keep with FRONT
green = opaque & (g > r + 15) & (g > bch + 15) & (g > 80)
plant = dilate(green, 2) & opaque
print("plant", int(plant.sum()))

above = np.zeros((H, W), dtype=bool)
seam = np.zeros((H, W), dtype=bool)
for x in range(W):
    fr = far_row_of(x)
    cut = int(np.floor(fr))
    above[: max(cut, 0), x] = True
    if 0 <= cut < H:
        seam[cut, x] = True
        if cut + 1 < H:
            seam[cut + 1, x] = True

# White desktop that the iso-formula calls "above the far-edge" but which
# still lives at y >= SPLIT (the leftover far-left/right wedge). Those
# pixels MUST stay in FRONT so they hide legs. Putting them in BACK is
# why people look like they sit on the tabletop.
white = opaque & (r > 180) & (g > 180) & (bch > 180)
rows = np.arange(H)[:, None]
wedge_white = white & above & (rows >= SPLIT)

# Near monitor / plant / front-chair cap live at y < SPLIT. FRONT's
# texture starts at SPLIT, so they MUST stay in BACK or they vanish.
front_chair_cap = front_chair & above
back_mask = opaque & ~wedge_white & (
    rear_chair | near_monitor | plant | front_chair_cap
    | (above & ~front_chair & ~near_monitor & ~plant)
)
front_mask = opaque & ~rear_chair & ~near_monitor & ~plant & (
    (~above) | seam | front_chair | wedge_white
)

new_back = np.zeros_like(full)
new_front_full = np.zeros_like(full)
new_back[back_mask] = full[back_mask]
new_front_full[front_mask] = full[front_mask]

# Harden seam alpha so the iso-edge doesn't show floor as an orange line
for img in (new_back, new_front_full):
    al = img[:, :, 3]
    img[:, :, 3] = np.where((al > 0) & (al < 255), 255, al)

ys = np.where(new_back[:, :, 3] > 8)[0]
back_h = int(ys.max()) + 1 if len(ys) else SPLIT
new_back_crop = new_back[:back_h]
new_front = new_front_full[SPLIT:]

print("wedge_white", int(wedge_white.sum()))
print("back", new_back_crop.shape, "front", new_front.shape,
      "back_h_world", round(back_h / PX, 1))

# Debug color overlay
dbg = full.copy()
dbg[back_mask & ~front_mask] = (40, 80, 220, 255)       # back only = blue
dbg[front_mask & ~back_mask] = (40, 200, 80, 255)       # front only = green
dbg[back_mask & front_mask] = (220, 200, 40, 255)       # overlap = yellow
dbg[rear_chair] = (220, 60, 220, 255)                   # rear chair = magenta
Image.fromarray(dbg).save(OUT / "layer_split_overlay.png")

Image.fromarray(new_back_crop).save(OUT / "new_back_chair.png")
Image.fromarray(new_front).save(OUT / "new_front_nowedge.png")

# Character
purple = Image.open(ASSETS / "agent-purple-typing-sheet.png").convert("RGBA")
disp = int(round(96 * 0.8 * PX))
char = purple.crop((0, 0, 96, 96)).resize((disp, disp), Image.Resampling.NEAREST)
WOOD = (210, 160, 105, 255)


def compose(dx, dy):
    cx = (dx - FRONT_LT[0]) * PX
    cy = (dy - BACK_LT[1]) * PX
    paste = (int(round(cx - 0.5 * disp)), int(round(cy - 0.875 * disp)))
    bg = Image.new("RGBA", (208, 170), WOOD)
    bg.paste(Image.fromarray(new_back_crop), (0, 0), Image.fromarray(new_back_crop))
    bg.paste(char, paste, char)
    bg.paste(Image.fromarray(new_front), (0, SPLIT), Image.fromarray(new_front))
    return bg, paste


# Sweep around chair center x≈-46 and waist-on-edge dy
dxs = [-52, -46, -40]
dys = [8, 14, 18, 22]
cell_w, cell_h = 216, 194
grid = Image.new("RGBA", (cell_w * len(dxs), cell_h * len(dys)), (40, 36, 32, 255))
draw = ImageDraw.Draw(grid)
for j, dy in enumerate(dys):
    for i, dx in enumerate(dxs):
        img, paste = compose(dx, dy)
        x, y = i * cell_w, j * cell_h
        grid.paste(img, (x, y))
        far = -20.8 + 0.5 * abs(dx)
        waist = dy - WAIST_ABOVE_FOOT
        draw.text(
            (x + 4, y + 2),
            f"dx={dx} dy={dy} far={far:.1f} waist={waist:.1f}",
            fill=(20, 20, 20, 255),
        )
grid.save(OUT / "seat_sweep_nowedge.png")

# 3x of the geometrically correct waist-on-edge seats
for dx, dy, name in (
    (-46, 18, "preview_nowedge_dx46_dy18.png"),
    (-46, 22, "preview_nowedge_dx46_dy22.png"),
    (-40, 18, "preview_nowedge_dx40_dy18.png"),
    (-52, 22, "preview_nowedge_dx52_dy22.png"),
):
    img, _ = compose(dx, dy)
    img.resize((208 * 3, 170 * 3), Image.Resampling.NEAREST).save(OUT / name)

# Two-person: A in rear chair + B in front chair (on TOP of FRONT, flipped)
char_b = ImageOps.mirror(char)
dx_b, dy_b = 43.5, 28.5
cx_b = (dx_b - FRONT_LT[0]) * PX
cy_b = (dy_b - BACK_LT[1]) * PX
paste_b = (int(round(cx_b - 0.5 * disp)), int(round(cy_b - 0.875 * disp)))
pair, _ = compose(-46, 18)
pair.paste(char_b, paste_b, char_b)
pair.resize((208 * 3, 170 * 3), Image.Resampling.NEAREST).save(OUT / "preview_pair_x3.png")
print("previews written")
