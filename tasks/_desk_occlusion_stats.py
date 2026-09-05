"""Quantitative + color-coded occlusion check for dx=-46, dy=18."""
from pathlib import Path
from PIL import Image
import numpy as np

OUT = Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug")
ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
PX = 208 / 159.2
FRONT_LT = (-78.2, -20.8)
BACK_LT = (-78.2, -62.1)
SPLIT = 54
disp = int(round(96 * 0.8 * PX))

back = np.array(Image.open(OUT / "new_back_chair.png").convert("RGBA"))
front = np.array(Image.open(OUT / "new_front_nowedge.png").convert("RGBA"))
purple = Image.open(ASSETS / "agent-purple-typing-sheet.png").convert("RGBA")
char_img = purple.crop((0, 0, 96, 96)).resize((disp, disp), Image.Resampling.NEAREST)
char = np.array(char_img)

dx, dy = -46.0, 18.0
cx = (dx - FRONT_LT[0]) * PX
cy = (dy - BACK_LT[1]) * PX
px = int(round(cx - 0.5 * disp))
py = int(round(cy - 0.875 * disp))
print("paste", px, py, "disp", disp, "char", char.shape, "back", back.shape, "front", front.shape)

canvas_h, canvas_w = 170, 208
layers = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
# place back
bh = back.shape[0]
layers[:bh, :back.shape[1]] = back
# place char
ch, cw = char.shape[:2]
for y in range(ch):
    for x in range(cw):
        if char[y, x, 3] < 16:
            continue
        yy, xx = py + y, px + x
        if 0 <= yy < canvas_h and 0 <= xx < canvas_w:
            layers[yy, xx] = char[y, x]
# FRONT on top
front_full = np.zeros_like(layers)
front_full[SPLIT:SPLIT+front.shape[0], :front.shape[1]] = front

# stats on character pixels
head_front = torso_front = legs_front = 0
head_n = torso_n = legs_n = 0
chair_behind_head = 0
for y in range(ch):
    rel = y / ch
    band = "head" if rel < 0.35 else ("torso" if rel < 0.62 else "legs")
    for x in range(cw):
        if char[y, x, 3] < 16:
            continue
        yy, xx = py + y, px + x
        if not (0 <= yy < canvas_h and 0 <= xx < canvas_w):
            continue
        covered = front_full[yy, xx, 3] > 16
        if band == "head":
            head_n += 1
            head_front += int(covered)
            if (not covered) and yy < back.shape[0] and back[yy, xx, 3] > 16:
                br, bg, bb = back[yy, xx, :3]
                if br < 90 and bg < 90 and bb < 100:
                    chair_behind_head += 1
        elif band == "torso":
            torso_n += 1
            torso_front += int(covered)
        else:
            legs_n += 1
            legs_front += int(covered)

print(f"head  covered by FRONT {head_front}/{head_n} = {head_front/max(head_n,1):.0%}")
print(f"torso covered by FRONT {torso_front}/{torso_n} = {torso_front/max(torso_n,1):.0%}")
print(f"legs  covered by FRONT {legs_front}/{legs_n} = {legs_front/max(legs_n,1):.0%}")
print(f"dark BACK (chair) visible in head band (FRONT transparent): {chair_behind_head}")

# Color diagnostic: char magenta, front lime @ 50%, back as-is
diag = Image.new("RGBA", (208, 170), (210, 160, 105, 255))
diag.paste(Image.fromarray(back), (0, 0), Image.fromarray(back))
mag = char.copy()
mag[char[:, :, 3] > 16] = (220, 40, 180, 220)
diag.paste(Image.fromarray(mag), (px, py), Image.fromarray(mag))
lime = front.copy()
vis = lime[:, :, 3] > 16
lime[vis, 0] = 40
lime[vis, 1] = 220
lime[vis, 2] = 80
lime[vis, 3] = 140
diag.paste(Image.fromarray(lime), (0, SPLIT), Image.fromarray(lime))
diag.resize((208 * 3, 170 * 3), Image.Resampling.NEAREST).save(OUT / "diag_occlusion_x3.png")

# natural compose again
nat = Image.new("RGBA", (208, 170), (210, 160, 105, 255))
nat.paste(Image.fromarray(back), (0, 0), Image.fromarray(back))
nat.paste(char_img, (px, py), char_img)
nat.paste(Image.fromarray(front), (0, SPLIT), Image.fromarray(front))
nat.resize((208 * 3, 170 * 3), Image.Resampling.NEAREST).save(OUT / "nat_dx46_dy18_x3.png")
print("wrote diag + nat")
