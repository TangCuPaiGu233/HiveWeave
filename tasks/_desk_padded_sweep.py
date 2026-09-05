"""Padded A-side sweep so the left chair isn't clipped by the 208 canvas."""
from pathlib import Path
from PIL import Image, ImageDraw
import numpy as np

OUT = Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug")
ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
PX = 208 / 159.2
FRONT_LT = (-78.2, -20.8)
BACK_LT = (-78.2, -62.1)
SPLIT = 54
PAD = 40
disp = int(round(96 * 0.8 * PX))

back = Image.open(OUT / "new_back_chair.png").convert("RGBA")
front = Image.open(OUT / "new_front_nowedge.png").convert("RGBA")
purple = Image.open(ASSETS / "agent-purple-typing-sheet.png").convert("RGBA")
char = purple.crop((0, 0, 96, 96)).resize((disp, disp), Image.Resampling.NEAREST)
WOOD = (210, 160, 105, 255)


def compose(dx, dy):
    cx = (dx - FRONT_LT[0]) * PX
    cy = (dy - BACK_LT[1]) * PX
    paste = (PAD + int(round(cx - 0.5 * disp)), PAD + int(round(cy - 0.875 * disp)))
    bg = Image.new("RGBA", (208 + PAD * 2, 170 + PAD * 2), WOOD)
    bg.paste(back, (PAD, PAD), back)
    bg.paste(char, paste, char)
    bg.paste(front, (PAD, PAD + SPLIT), front)
    return bg, paste

dxs = [-58, -52, -46, -40]
dys = [16, 18, 20]
cw, ch = 208 + PAD * 2 + 8, 170 + PAD * 2 + 22
grid = Image.new("RGBA", (cw * len(dxs), ch * len(dys)), (35, 32, 30, 255))
draw = ImageDraw.Draw(grid)
for j, dy in enumerate(dys):
    for i, dx in enumerate(dxs):
        img, _ = compose(dx, dy)
        x, y = i * cw, j * ch
        grid.paste(img, (x, y))
        far = -20.8 + 0.5 * abs(dx)
        draw.text((x + 6, y + 4), f"dx={dx} dy={dy} far={far:.1f} waist={dy-19.2:.1f}", fill=(15, 15, 15, 255))
grid.save(OUT / "padded_sweep.png")
img, _ = compose(-52, 18)
img.resize((img.width * 2, img.height * 2), Image.Resampling.NEAREST).save(OUT / "padded_dx52_dy18.png")
img, _ = compose(-46, 18)
img.resize((img.width * 2, img.height * 2), Image.Resampling.NEAREST).save(OUT / "padded_dx46_dy18.png")
print("ok")
