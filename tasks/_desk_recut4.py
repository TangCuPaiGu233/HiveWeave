"""Recut FRONT so its top follows the isometric far-edge (slope 0.5),
and keep the monitor wholly in BACK.

Matches office-scene-calibration SKILL:
  far_edge(rel_x) = -20.8 + 0.5 * abs(rel_x)
"""
from pathlib import Path
from PIL import Image
import numpy as np

ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
OUT = Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug")
SPLIT = 54
PX = 208 / 159.2  # px per world
FRONT_LT = (-78.2, -20.8)
BACK_LT = (-78.2, -62.1)

full = np.array(Image.open(ASSETS / "office-desk-set.png").convert("RGBA"))
cur_back = np.array(Image.open(ASSETS / "office-desk-back.png").convert("RGBA"))
H, W = full.shape[:2]
a = full[:, :, 3] > 8
r = full[:, :, 0].astype(np.int16)
g = full[:, :, 1].astype(np.int16)
bch = full[:, :, 2].astype(np.int16)

# --- monitor (tight ROI from blue histogram: y=0-80, x=92-121) ---
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
print("monitor", int(monitor.sum()),
      "bbox", (int(np.where(monitor)[1].min()), int(np.where(monitor)[0].min()),
               int(np.where(monitor)[1].max()), int(np.where(monitor)[0].max())))

# --- FRONT: start from full lower half, then:
# 1) clear pixels above the isometric far-edge in each column
# 2) clear monitor (moved to BACK)
new_front = full[SPLIT:].copy()
cleared = 0
for x in range(W):
    rel_x = FRONT_LT[0] + x / PX  # world x relative to desk origin
    far_y = -20.8 + 0.5 * abs(rel_x)  # world y of far edge
    # row in FULL image
    far_row_full = (far_y - BACK_LT[1]) * PX
    far_row_front = far_row_full - SPLIT
    cut = int(np.floor(far_row_front))
    if cut > 0:
        new_front[:cut, x] = 0
        cleared += cut
new_front[monitor[SPLIT:]] = 0
print("front rows cleared above iso-edge:", cleared, "opaque left", int((new_front[:,:,3]>8).sum()))

# --- BACK: current back at x-off 3, plus monitor, plus the FRONT pixels we cleared
#     that belong to the far desktop strip? Those cleared pixels ARE the far part of
#     the desktop — they should stay in BACK (behind the character) so the far
#     tabletop isn't missing. Stamp full[0:SPLIT] already in current back.
#     For rows >= SPLIT that we cleared from FRONT (iso-edge wedge), stamp them into BACK
#     UNLESS they are in the character's sitting column and are desktop covering the chest.
#     Actually: those pixels are the far tabletop. They should be BEHIND the character
#     (BACK) so the character's chest shows, and the visible far tabletop appears
#     around the character. Yes — stamp the iso-wedge into BACK.
new_back = np.zeros_like(full)
new_back[:SPLIT, 3:3+cur_back.shape[1]] = cur_back
new_back[monitor] = full[monitor]
# iso-wedge: pixels we zeroed in FRONT that aren't monitor
wedge = np.zeros((H, W), dtype=bool)
for x in range(W):
    rel_x = FRONT_LT[0] + x / PX
    far_y = -20.8 + 0.5 * abs(rel_x)
    far_row_full = (far_y - BACK_LT[1]) * PX
    cut = int(np.floor(far_row_full - SPLIT))
    if cut > 0:
        wedge[SPLIT:SPLIT+cut, x] = True
wedge &= a
wedge &= ~monitor
new_back[wedge] = full[wedge]
ys = np.where(new_back[:, :, 3] > 8)[0]
back_h = int(ys.max()) + 1
new_back = new_back[:back_h]
print("new back", new_back.shape, "wedge", int(wedge.sum()))

Image.fromarray(new_back).save(OUT / "new_back.png")
Image.fromarray(new_front).save(OUT / "new_front.png")

# preview
purple = Image.open(ASSETS / "agent-purple-typing-sheet.png").convert("RGBA")
disp = int(round(96 * 0.8 * PX))
char = purple.crop((0, 0, 96, 96)).resize((disp, disp), Image.Resampling.NEAREST)

def compose(dx, dy, name):
    cx = (dx - FRONT_LT[0]) * PX
    cy = (dy - BACK_LT[1]) * PX
    paste = (int(round(cx - 0.5 * disp)), int(round(cy - 0.875 * disp)))
    bg = Image.new("RGBA", (208, 170), (58, 48, 40, 255))
    bg.paste(Image.fromarray(new_back), (0, 0), Image.fromarray(new_back))
    bg.paste(char, paste, char)
    bg.paste(Image.fromarray(new_front), (0, SPLIT), Image.fromarray(new_front))
    bg.save(OUT / name)
    print(name, "paste", paste, "far_edge_at_col", -20.8 + 0.5 * abs(dx))

compose(-40, 8, "preview_iso_dx40_dy8.png")
compose(-40, 18, "preview_iso_dx40_dy18.png")
print("DESK_SET.back w", round(W / PX, 1), "h", round(back_h / PX, 1), "leftTop", BACK_LT)
print("done")
