"""Recut desk sprites: keep monitors/partition wholly in BACK, FRONT = desktop+chairs+legs only.

Then composite: BACK -> character -> FRONT to preview occlusion.
"""
from pathlib import Path
from PIL import Image
import numpy as np

ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
OUT = Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug")
SCALE = 208 / 159.2

full = np.array(Image.open(ASSETS / "office-desk-set.png").convert("RGBA"))
H, W = full.shape[:2]
SPLIT = 54  # current back height / front top
a = full[:, :, 3] > 8
r = full[:, :, 0].astype(np.int16)
g = full[:, :, 1].astype(np.int16)
b = full[:, :, 2].astype(np.int16)

# --- tall props: blue monitor + dark bezels + left screen + vertical panel ---
blue = a & (b > 80) & (b > r + 12) & (b > g - 5)
print("blue", int(blue.sum()))

def dilate(m, rad=3):
    out = m.copy()
    ys, xs = np.where(m)
    for dy in range(-rad, rad + 1):
        for dx in range(-rad, rad + 1):
            yy, xx = ys + dy, xs + dx
            ok = (yy >= 0) & (yy < H) & (xx >= 0) & (xx < W)
            out[yy[ok], xx[ok]] = True
    return out

mon = dilate(blue, 5) & a
darkish = (r < 150) & (g < 150) & (b < 190)
mon = (blue | (mon & darkish)) & a

# vertical panel: columns with opaque pixels in the TOP 16 rows (props stick up)
top_cols = np.where(a[:16].any(axis=0))[0]
print("top-row columns", int(top_cols.min()) if len(top_cols) else None, int(top_cols.max()) if len(top_cols) else None)

# For each column that has content in the top 20px, the object is "tall".
# Grow downward while pixel is not "wide desktop".
tall = mon.copy()
# seed from any opaque pixel in rows 0-20
tall[:21] |= a[:21]

# grow down column-wise: continue while this col is opaque and
# either (narrow structure) or (already marked tall neighbor)
for y in range(1, H):
    for x in range(W):
        if not a[y, x] or tall[y, x]:
            continue
        above = tall[y - 1, x] or (x > 0 and tall[y - 1, x - 1]) or (x + 1 < W and tall[y - 1, x + 1])
        if not above:
            continue
        # stop if this row looks like a wide white desktop band at this x
        # (many white neighbors) AND we're past the split — that's the tabletop
        white_here = r[y, x] > 190 and g[y, x] > 185 and b[y, x] > 175 and abs(int(r[y, x]) - int(g[y, x])) < 35
        if y >= SPLIT and white_here:
            # count white in a horizontal window
            x0, x1 = max(0, x - 20), min(W, x + 21)
            wh = ((r[y, x0:x1] > 190) & (g[y, x0:x1] > 185) & (full[y, x0:x1, 3] > 8)).sum()
            if wh >= 18:
                continue  # desktop — don't eat it
        tall[y, x] = True

print("tall px", int(tall.sum()), "in front half", int(tall[SPLIT:].sum()), "in back half", int(tall[:SPLIT].sum()))

# Visualize tall on original
vis = full.copy()
vis[tall] = (vis[tall] * 0.4 + np.array([255, 40, 40, 255]) * 0.6).astype(np.uint8)
Image.fromarray(vis).save(OUT / "tall_overlay.png")

# --- build new BACK: full size 208x170, opaque = original back content + tall ---
new_back = np.zeros_like(full)
new_back[:SPLIT] = full[:SPLIT]  # keep current back half (chair already gone on left of original set? set still has chair)
# The set INCLUDES the rear chair. Current office-desk-back.png punched x<94.
# Re-apply that punch on the top half so the character isn't doubled with a chair.
# (x<94 in rows 0:SPLIT was the rear-chair region in the 201-wide back; full is 208 wide,
#  back was offset by ~3px → punch x<97 in full coords)
new_back[:SPLIT, :97] = 0
# add tall props everywhere (restores monitor/partition in the punched region if any)
new_back[tall] = full[tall]
# crop to opaque bbox later

# --- build new FRONT: lower half, minus tall ---
new_front = full[SPLIT:].copy()
new_front[tall[SPLIT:]] = 0

Image.fromarray(new_back).save(OUT / "new_back.png")
Image.fromarray(new_front).save(OUT / "new_front.png")

def bbox(arr):
    m = arr[:, :, 3] > 8
    ys, xs = np.where(m)
    if len(ys) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1

print("new_back bbox", bbox(new_back), "opaque", int((new_back[:,:,3]>8).sum()))
print("new_front bbox", bbox(new_front), "opaque", int((new_front[:,:,3]>8).sum()))

# --- composite preview with character ---
purple = Image.open(ASSETS / "agent-purple-typing-sheet.png").convert("RGBA")
frame0 = purple.crop((0, 0, 96, 96))
disp = int(round(96 * 0.8 * SCALE))
char = frame0.resize((disp, disp), Image.Resampling.NEAREST)

def world_to_full(wx, wy):
    s = 159.2 / 208
    return (wx - (-78.2)) / s, (wy - (-62.1)) / s

dx, dy = -40.0, 8.0
cx, cy = world_to_full(dx, dy)
paste = (int(round(cx - 0.5 * disp)), int(round(cy - 0.875 * disp)))

comp = Image.new("RGBA", (208, 170), (36, 32, 30, 255))
comp.paste(Image.fromarray(new_back), (0, 0), Image.fromarray(new_back))
comp.paste(char, paste, char)
comp.paste(Image.fromarray(new_front), (0, SPLIT), Image.fromarray(new_front))
comp.save(OUT / "preview_recut_dx40_dy8.png")
print("composite paste", paste)

# also try dy=18 (waist at far-edge)
dx, dy = -40.0, 18.0
cx, cy = world_to_full(dx, dy)
paste = (int(round(cx - 0.5 * disp)), int(round(cy - 0.875 * disp)))
comp2 = Image.new("RGBA", (208, 170), (36, 32, 30, 255))
comp2.paste(Image.fromarray(new_back), (0, 0), Image.fromarray(new_back))
comp2.paste(char, paste, char)
comp2.paste(Image.fromarray(new_front), (0, SPLIT), Image.fromarray(new_front))
comp2.save(OUT / "preview_recut_dx40_dy18.png")
print("dy18 paste", paste)
print("done")
