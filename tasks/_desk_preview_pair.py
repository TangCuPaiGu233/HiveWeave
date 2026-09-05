"""Padded B-side sit-on-chair preview (character drawn after FRONT)."""
from pathlib import Path
from PIL import Image, ImageOps

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
char_b = ImageOps.mirror(char)
WOOD = (210, 160, 105, 255)


def compose(dx_a, dy_a, dx_b, dy_b, flip, name):
    def paste_of(dx, dy):
        cx = (dx - FRONT_LT[0]) * PX
        cy = (dy - BACK_LT[1]) * PX
        return (PAD + int(round(cx - 0.5 * disp)), PAD + int(round(cy - 0.875 * disp)))
    bg = Image.new("RGBA", (208 + PAD * 2, 170 + PAD * 2), WOOD)
    bg.paste(back, (PAD, PAD), back)
    bg.paste(char, paste_of(dx_a, dy_a), char)
    bg.paste(front, (PAD, PAD + SPLIT), front)
    spr = char_b if flip else char
    bg.paste(spr, paste_of(dx_b, dy_b), spr)
    bg.resize((bg.width * 2, bg.height * 2), Image.Resampling.NEAREST).save(OUT / name)

# A at new seat, B at calibrated front chair
compose(-50, 21, 43.5, 28.5, True, "pair_A50_21_B43_28_flip.png")
compose(-50, 21, 43.5, 36, True, "pair_A50_21_B43_36_flip.png")
compose(-50, 21, 38, 32, True, "pair_A50_21_B38_32_flip.png")
compose(-50, 21, 43.5, 28.5, False, "pair_A50_21_B43_28_nflip.png")
print("ok")
