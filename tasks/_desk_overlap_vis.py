"""Visualize where the seated character overlaps the current front/back split."""
from pathlib import Path
from PIL import Image, ImageDraw
import numpy as np

ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
OUT = Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug")
OUT.mkdir(exist_ok=True)

SCALE = 1.30625  # px per world unit (1672/1280)

full = Image.open(ASSETS / "office-desk-set.png").convert("RGBA")
front = Image.open(ASSETS / "office-desk-front.png").convert("RGBA")
back = Image.open(ASSETS / "office-desk-back.png").convert("RGBA")
purple = Image.open(ASSETS / "agent-purple-typing-sheet.png").convert("RGBA")

# Geometry from constants.ts (world units)
desk = {"x": 0, "y": 0}  # relative
back_lt = {"x": -75.9, "y": -62.1, "w": 153.9, "h": 41.3}
front_lt = {"x": -78.2, "y": -20.8, "w": 159.2, "h": 88.8}
rear_chair = {"dx": -40.0, "dy": 8.0}

# Character: frame 0 of 2x2 96x96, scale 0.8, anchor 0.5 / 0.875
fw, fh = 96, 96
frame0 = purple.crop((0, 0, fw, fh))
char_disp_w = fw * 0.8
char_disp_h = fh * 0.8
# In source px space (desk-set is 208x170 = front width x (back.h+front.h))
# Map world → desk-set px: origin = desk slot = world (0,0)
# desk-set image: we need to know where world (0,0) falls in the 208x170 image.
# front sprite leftTop world (-78.2, -20.8) maps to pixel (0, 54) of the full 208x170
# because full rows 0:54 = back, 54:170 = front.
# So pixel (px, py) in full image:
#   world_x = -78.2 + px * (159.2/208)
#   world_y = -62.1 + py * (41.3/54)   for py<54  ... wait, back has different width/scale
# Back is 201x54 displayed as 153.9x41.3, leftTop (-75.9, -62.1)
# Front is 208x116 displayed as 159.2x88.8, leftTop (-78.2, -20.8)
#
# These should be the same scale 1.30625:
print("front scale x", 208 / 159.2, "y", 116 / 88.8)
print("back  scale x", 201 / 153.9, "y", 54 / 41.3)

# Use front's mapping as canonical (208 px = 159.2 world, origin at front leftTop)
# Full image is 208 wide; back is 201 wide starting... 
# back leftTop x=-75.9 vs front leftTop x=-78.2 → back is 2.3 world = 3.0 px to the right of front's left
# So back pixel 0 corresponds to full pixel ~3.

def world_to_full(wx, wy):
    # Use front mapping extended to full image:
    # full pixel (0, 54) = world (-78.2, -20.8)
    # 1 px = 159.2/208 world
    s = 159.2 / 208
    px = (wx - (-78.2)) / s
    py = (wy - (-62.1)) / s   # extend same scale upward; back h 41.3 / s = 54.0 yes!
    return px, py

print("split line full-px y", world_to_full(0, -20.8)[1])
print("rear chair foot full-px", world_to_full(-40, 8))
print("char top full-px", world_to_full(-40, 8 - 0.875 * char_disp_h))
print("char left/right", world_to_full(-40 - char_disp_w/2, 8), world_to_full(-40 + char_disp_w/2, 8))
print("waist full-px", world_to_full(-40, 8 - 19.2))

# Composite visualization on full desk
vis = Image.new("RGBA", (208, 170), (30, 30, 30, 255))
vis.paste(full, (0, 0), full)

# Draw split line
d = ImageDraw.Draw(vis)
split_y = world_to_full(0, -20.8)[1]
d.line([(0, split_y), (207, split_y)], fill=(255, 0, 0, 255), width=1)

# Paste character at seat
cx, cy = world_to_full(-40, 8)  # foot anchor
# sprite top-left in full px: foot is at 0.875 of displayed height
# displayed size in full-px: char_disp * (208/159.2) = char_disp * SCALE wait
s = 208 / 159.2
disp_w = char_disp_w * s
disp_h = char_disp_h * s
char_img = frame0.resize((int(round(disp_w)), int(round(disp_h))), Image.Resampling.NEAREST)
anchor_x = 0.5 * disp_w
anchor_y = 0.875 * disp_h
paste_x = int(round(cx - anchor_x))
paste_y = int(round(cy - anchor_y))
print(f"char paste at ({paste_x},{paste_y}) size {char_img.size} foot=({cx:.1f},{cy:.1f})")

overlay = Image.new("RGBA", vis.size, (0, 0, 0, 0))
overlay.paste(char_img, (paste_x, paste_y), char_img)
# tint character red-ish to see overlap
arr = np.array(overlay)
mask = arr[:, :, 3] > 0
arr[mask, 0] = np.minimum(255, arr[mask, 0].astype(np.int16) + 80)
arr[mask, 1] = (arr[mask, 1] * 0.5).astype(np.uint8)
vis2 = Image.alpha_composite(vis, Image.fromarray(arr))

# Highlight FRONT opaque pixels that overlap character
front_on_full = Image.new("RGBA", (208, 170), (0, 0, 0, 0))
front_on_full.paste(front, (0, 54), front)
farr = np.array(front_on_full)
carr = np.array(overlay)
overlap = (farr[:, :, 3] > 8) & (carr[:, :, 3] > 8)
print(f"FRONT∩character opaque pixels: {int(overlap.sum())}")
ys, xs = np.where(overlap)
if len(ys):
    print(f"  overlap bbox x={xs.min()}..{xs.max()} y={ys.min()}..{ys.max()}")

# Paint overlap yellow
vis3 = np.array(vis2).copy()
vis3[overlap] = (255, 255, 0, 255)
Image.fromarray(vis3).save(OUT / "overlap_front_char.png")
vis2.save(OUT / "seat_on_desk.png")

# Color-segment the full desk for recut planning
f = np.array(full)
a = f[:, :, 3] > 8
r, g, b = f[:, :, 0], f[:, :, 1], f[:, :, 2]
# white-ish desk/partition
white = a & (r > 180) & (g > 180) & (b > 180)
# blue monitor
blue = a & (b > 80) & (b > r + 20) & (b > g)
# dark chair
dark = a & (r < 80) & (g < 80) & (b < 80)
# green plant
green = a & (g > r + 15) & (g > b + 15) & (g > 60)
# grey metal legs
grey = a & ~white & ~blue & ~dark & ~green

seg = np.zeros_like(f)
seg[white] = (240, 240, 240, 255)
seg[blue] = (40, 80, 220, 255)
seg[dark] = (20, 20, 20, 255)
seg[green] = (40, 200, 60, 255)
seg[grey] = (160, 80, 80, 255)
Image.fromarray(seg).save(OUT / "color_seg.png")
print("white", int(white.sum()), "blue", int(blue.sum()), "dark", int(dark.sum()),
      "green", int(green.sum()), "grey", int(grey.sum()))

# Column profile of white pixels (find partition x)
white_cols = white.sum(axis=0)
top30 = white[:54].sum(axis=0)  # white in BACK half
print("top-half white col peaks:", list(np.where(top30 > 10)[0][:20]), "... max", int(top30.max()), "at", int(top30.argmax()))

# Save labeled split view
split_vis = np.array(full).copy()
split_vis[:54, :, 0] = np.minimum(255, split_vis[:54, :, 0] + 60)  # back tinted red
split_vis[54:, :, 2] = np.minimum(255, split_vis[54:, :, 2] + 60)  # front tinted blue
Image.fromarray(split_vis).save(OUT / "current_split.png")
print("wrote", OUT)
