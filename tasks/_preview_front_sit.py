"""Preview: sit characters IN the visible front chair (z above FRONT), optional flip."""
from pathlib import Path
from PIL import Image, ImageOps
import numpy as np

ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
OUT = Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug")
PX = 208 / 159.2
FRONT_LT = (-78.2, -20.8)
BACK_LT = (-78.2, -62.1)
SPLIT = 54

full = np.array(Image.open(ASSETS / "office-desk-set.png").convert("RGBA"))
back = np.array(Image.open(OUT / "new_back_with_chair.png").convert("RGBA"))
front = np.array(Image.open(ASSETS / "office-desk-front.png").convert("RGBA"))
purple = Image.open(ASSETS / "agent-purple-typing-sheet.png").convert("RGBA")
disp = int(round(96 * 0.8 * PX))
char = purple.crop((0, 0, 96, 96)).resize((disp, disp), Image.Resampling.NEAREST)
char_flip = ImageOps.mirror(char)

def compose(dx, dy, flip, name):
    spr = char_flip if flip else char
    cx = (dx - FRONT_LT[0]) * PX
    cy = (dy - BACK_LT[1]) * PX
    paste = (int(round(cx - 0.5 * disp)), int(round(cy - 0.875 * disp)))
    bg = Image.new("RGBA", (208, 170), (210, 160, 105, 255))
    bg.paste(Image.fromarray(back), (0, 0), Image.fromarray(back))
    bg.paste(Image.fromarray(front), (0, SPLIT), Image.fromarray(front))
    # character ON TOP of front (sitting in visible chair)
    bg.paste(spr, paste, spr)
    bg.resize((208 * 3, 170 * 3), Image.Resampling.NEAREST).save(OUT / name)
    print(name, "paste", paste)

# current rear (for comparison)
compose(-40, 8, False, "cmp_rear_dx40_dy8.png")
# front chair calibrated
compose(43.5, 28.5, False, "cmp_front_nflip.png")
compose(43.5, 28.5, True, "cmp_front_flip.png")
# slight nudges
compose(43.5, 22, True, "cmp_front_flip_dy22.png")
compose(38, 28.5, True, "cmp_front_flip_dx38.png")
compose(50, 32, True, "cmp_front_flip_dx50_dy32.png")
print("done")
