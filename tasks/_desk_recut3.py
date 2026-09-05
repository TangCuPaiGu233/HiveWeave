"""Move the computer monitor wholly into BACK. FRONT keeps desktop/chairs/legs.

Aligns to office-desk-set.png 208x170 space (world leftTop = DESK_SET front).
"""
from pathlib import Path
from PIL import Image
import numpy as np

ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
OUT = Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug")
SPLIT = 54

full = np.array(Image.open(ASSETS / "office-desk-set.png").convert("RGBA"))
cur_back = np.array(Image.open(ASSETS / "office-desk-back.png").convert("RGBA"))
H, W = full.shape[:2]
r = full[:, :, 0].astype(np.int16)
g = full[:, :, 1].astype(np.int16)
bch = full[:, :, 2].astype(np.int16)
a = full[:, :, 3] > 8

# Match current back against full[0:54] to find x offset
best_off, best_score = 0, -1
band = full[0:SPLIT]
for off in range(0, W - cur_back.shape[1] + 1):
    sl = band[:, off : off + cur_back.shape[1]]
    # compare opaque pixels
    mb = cur_back[:, :, 3] > 8
    mf = sl[:, :, 3] > 8
    both = mb & mf
    if both.sum() < 100:
        continue
    diff = np.abs(cur_back[:, :, :3][both].astype(int) - sl[:, :, :3][both].astype(int)).mean()
    score = int(both.sum()) - diff * 10
    if score > best_score:
        best_score, best_off = score, off
print("back x-offset vs full", best_off, "score", best_score)

blue = a & (bch > 90) & (bch > r + 8) & (bch > g - 10)
# keep monitor in the center stack; drop the blue bottle on the far side if any
# bottle was described near x=110 top — that's the monitor area. OK.
def dilate(m, rad):
    out = np.zeros_like(m)
    ys, xs = np.where(m)
    for dy in range(-rad, rad + 1):
        for dx in range(-rad, rad + 1):
            yy, xx = ys + dy, xs + dx
            ok = (yy >= 0) & (yy < H) & (xx >= 0) & (xx < W)
            out[yy[ok], xx[ok]] = True
    return out

# bezel: dark pixels near blue, but not the chairs (chairs are lower-left / lower-right)
near = dilate(blue, 5) & a
dark = (r < 90) & (g < 90) & (bch < 110)
bezel = near & dark
# don't eat chairs: chairs have y > 90 or x < 70
bezel = bezel & (np.arange(H)[:, None] < 90) & (np.arange(W)[None, :] > 70)
monitor = (blue | bezel) & a
print("monitor px", int(monitor.sum()), "bbox",
      tuple(int(x) for x in (np.where(monitor)[1].min(), np.where(monitor)[0].min(),
                               np.where(monitor)[1].max(), np.where(monitor)[0].max())))

# --- new BACK in full coords ---
new_back = np.zeros_like(full)
# paste current back at discovered offset (keeps rear-chair punch)
new_back[:SPLIT, best_off : best_off + cur_back.shape[1]] = cur_back
# stamp full monitor (both halves)
new_back[monitor] = full[monitor]
ys = np.where(new_back[:, :, 3] > 8)[0]
back_h = int(ys.max()) + 1
new_back = new_back[:back_h]
print("new back", new_back.shape)

# --- new FRONT: current front minus monitor ---
new_front = full[SPLIT:].copy()
new_front[monitor[SPLIT:]] = 0

Image.fromarray(new_back).save(OUT / "new_back.png")
Image.fromarray(new_front).save(OUT / "new_front.png")
ov = full.copy()
ov[monitor] = (np.array([40, 180, 255, 255]) * 0.5 + ov[monitor] * 0.5).astype(np.uint8)
Image.fromarray(ov).save(OUT / "monitor_overlay.png")

# composite
SCALE = 208 / 159.2
purple = Image.open(ASSETS / "agent-purple-typing-sheet.png").convert("RGBA")
char = purple.crop((0, 0, 96, 96)).resize(
    (int(round(96 * 0.8 * SCALE)), int(round(96 * 0.8 * SCALE))), Image.Resampling.NEAREST
)
disp = char.size[0]

def world_to_full(wx, wy):
    s = 159.2 / 208
    return (wx + 78.2) / s, (wy + 62.1) / s

def compose(dx, dy, name):
    cx, cy = world_to_full(dx, dy)
    paste = (int(round(cx - 0.5 * disp)), int(round(cy - 0.875 * disp)))
    bg = Image.new("RGBA", (208, 170), (50, 42, 36, 255))
    bg.paste(Image.fromarray(new_back), (0, 0), Image.fromarray(new_back))
    bg.paste(char, paste, char)
    bg.paste(Image.fromarray(new_front), (0, SPLIT), Image.fromarray(new_front))
    bg.save(OUT / name)
    print(name, "paste", paste)

compose(-40, 8, "preview_mon_dx40_dy8.png")
compose(-32, 8, "preview_mon_dx32_dy8.png")

# print new DESK_SET numbers (world = px / 1.30625, origin = full (0,0) at world (-78.2, -62.1))
px_per_world = 208 / 159.2
print("DESK_SET.back  w", round(W / px_per_world, 1), "h", round(back_h / px_per_world, 1),
      "leftTop", (-78.2, -62.1))
print("DESK_SET.front unchanged")
print("done")
