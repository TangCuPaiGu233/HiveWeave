"""Restore rear chair into BACK and sweep seat positions vs the natural sitting look.

Target (user's 2nd image): character sits IN the rear chair, waist at desk far-edge,
legs hidden by desktop, chair backrest peeking behind shoulders.
"""
from pathlib import Path
from PIL import Image, ImageDraw
import numpy as np

ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
OUT = Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug")
OUT.mkdir(exist_ok=True)

SPLIT = 54
PX = 208 / 159.2
FRONT_LT = (-78.2, -20.8)
BACK_LT = (-78.2, -62.1)

full = np.array(Image.open(ASSETS / "office-desk-set.png").convert("RGBA"))
cur_back = np.array(Image.open(ASSETS / "office-desk-back.png").convert("RGBA"))
cur_front = np.array(Image.open(ASSETS / "office-desk-front.png").convert("RGBA"))
H, W = full.shape[:2]
r, g, b, a = full[:,:,0], full[:,:,1], full[:,:,2], full[:,:,3]
opaque = a > 8
dark = opaque & (r < 85) & (g < 85) & (b < 95)

# Rear chair: left dark blob (not the front chair which is x>~90, y>~70)
rear_region = np.zeros((H, W), dtype=bool)
rear_region[:, :92] = True
rear_dark = dark & rear_region
# drop tiny specks: keep rows that have a run
print("rear dark px", int(rear_dark.sum()),
      "bbox", (int(np.where(rear_dark)[1].min()), int(np.where(rear_dark)[0].min()),
               int(np.where(rear_dark)[1].max()), int(np.where(rear_dark)[0].max())))

# flood from a seed in the chair back (around x=40, y=25)
from collections import deque
def flood(seed_mask, pred):
    vis = np.zeros((H, W), dtype=bool)
    ys, xs = np.where(seed_mask)
    q = deque(zip(ys.tolist()[:80], xs.tolist()[:80]))
    n = 0
    while q and n < 30000:
        y, x = q.popleft()
        if y<0 or y>=H or x<0 or x>=W or vis[y,x] or not pred[y,x]:
            continue
        vis[y,x] = True
        n += 1
        q.extend(((y-1,x),(y+1,x),(y,x-1),(y,x+1),(y-1,x-1),(y-1,x+1),(y+1,x-1),(y+1,x+1)))
    return vis

seed = np.zeros((H,W), bool)
seed[15:45, 25:70] = rear_dark[15:45, 25:70]
chair = flood(seed, rear_dark)
print("chair flood", int(chair.sum()),
      "bbox", (int(np.where(chair)[1].min()), int(np.where(chair)[0].min()),
               int(np.where(chair)[1].max()), int(np.where(chair)[0].max())))

# dilate 1px to pick anti-aliased chair edge
def dilate(m, rad=1):
    out = m.copy()
    ys, xs = np.where(m)
    for dy in range(-rad, rad+1):
        for dx in range(-rad, rad+1):
            yy, xx = ys+dy, xs+dx
            ok = (yy>=0)&(yy<H)&(xx>=0)&(xx<W)
            out[yy[ok], xx[ok]] = True
    return out
chair_d = dilate(chair, 1) & opaque

vis = full.copy()
vis[chair_d] = (np.array([40, 220, 80, 255])*0.55 + vis[chair_d]*0.45).astype(np.uint8)
Image.fromarray(vis).save(OUT / "chair_mask.png")

# Build new BACK: current back + chair from full (chair may extend below SPLIT)
new_back = np.zeros_like(full)
new_back[:cur_back.shape[0], :cur_back.shape[1]] = cur_back
# current back is 208x103 already
if cur_back.shape[0] != H:
    # pad
    nb = np.zeros_like(full)
    nb[:cur_back.shape[0], :min(W, cur_back.shape[1])] = cur_back[:, :min(W, cur_back.shape[1])]
    new_back = nb
new_back[chair_d] = full[chair_d]
ys = np.where(new_back[:,:,3] > 8)[0]
back_h = int(ys.max()) + 1
new_back = new_back[:back_h]
print("new back", new_back.shape)

Image.fromarray(new_back).save(OUT / "new_back_with_chair.png")

# Character
purple = Image.open(ASSETS / "agent-purple-typing-sheet.png").convert("RGBA")
disp = int(round(96 * 0.8 * PX))
char = purple.crop((0, 0, 96, 96)).resize((disp, disp), Image.Resampling.NEAREST)

def compose(dx, dy):
    cx = (dx - FRONT_LT[0]) * PX
    cy = (dy - BACK_LT[1]) * PX
    paste = (int(round(cx - 0.5 * disp)), int(round(cy - 0.875 * disp)))
    bg = Image.new("RGBA", (208, 170), (210, 160, 105, 255))
    bg.paste(Image.fromarray(new_back), (0, 0), Image.fromarray(new_back))
    bg.paste(char, paste, char)
    bg.paste(Image.fromarray(cur_front), (0, SPLIT), Image.fromarray(cur_front))
    return bg, paste

# sweep
dxs = [-52, -46, -40]
dys = [8, 14, 18, 24]
cell_w, cell_h = 208 + 8, 170 + 24
grid = Image.new("RGBA", (cell_w * len(dxs), cell_h * len(dys)), (30, 28, 26, 255))
draw = ImageDraw.Draw(grid)
for j, dy in enumerate(dys):
    for i, dx in enumerate(dxs):
        img, paste = compose(dx, dy)
        x, y = i * cell_w, j * cell_h
        grid.paste(img, (x, y))
        far = -20.8 + 0.5 * abs(dx)
        waist = dy - 19.2
        draw.text((x + 4, y + 2), f"dx={dx} dy={dy} paste={paste}\nfar={far:.1f} waist={waist:.1f}", fill=(20, 20, 20, 255))
grid.save(OUT / "seat_sweep_chair.png")
print("wrote seat_sweep_chair.png")

# also a 3x scale of the most promising (dx=-46, dy=18)
img, _ = compose(-46, 18)
img.resize((208*3, 170*3), Image.Resampling.NEAREST).save(OUT / "preview_sit_x3.png")
img, _ = compose(-40, 18)
img.resize((208*3, 170*3), Image.Resampling.NEAREST).save(OUT / "preview_sit_dx40_dy18_x3.png")
print("done")
