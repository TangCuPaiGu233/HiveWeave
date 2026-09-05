"""Preview: full desk + character with lower body clipped (no horizontal desk split)."""
from pathlib import Path
from PIL import Image, ImageDraw
import numpy as np

ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
OUT = Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug")
SCALE = 208 / 159.2  # px per world

full = Image.open(ASSETS / "office-desk-set.png").convert("RGBA")
purple = Image.open(ASSETS / "agent-purple-typing-sheet.png").convert("RGBA")
frame0 = purple.crop((0, 0, 96, 96))

def world_to_full(wx, wy):
    s = 159.2 / 208
    return (wx - (-78.2)) / s, (wy - (-62.1)) / s

def composite(dx, dy, clip_above_foot, label):
    vis = Image.new("RGBA", (208, 170), (40, 36, 32, 255))
    vis.paste(full, (0, 0), full)
    disp = 96 * 0.8 * SCALE  # ~100
    char = frame0.resize((int(round(disp)), int(round(disp))), Image.Resampling.NEAREST)
    arr = np.array(char)
    # clip: hide pixels below (foot - clip_above_foot) in local; foot at 0.875*h
    h = arr.shape[0]
    foot_row = 0.875 * h
    clip_row = foot_row - clip_above_foot * SCALE
    cut = max(0, min(h, int(round(clip_row))))
    arr[cut:, :, 3] = 0
    char = Image.fromarray(arr)
    cx, cy = world_to_full(dx, dy)
    paste = (int(round(cx - 0.5 * disp)), int(round(cy - 0.875 * disp)))
    vis.paste(char, paste, char)
    d = ImageDraw.Draw(vis)
    d.line([(0, world_to_full(0, -20.8)[1]), (207, world_to_full(0, -20.8)[1])], fill=(255, 60, 60, 180))
    vis.save(OUT / f"preview_{label}.png")
    print(f"wrote preview_{label}.png  seat=({dx},{dy}) clip={clip_above_foot} paste={paste} cut_row={cut}/{h}")

# variants
composite(-40, 8, 19.2, "dx40_dy8_waist")
composite(-40, 8, 28.0, "dx40_dy8_chest")  # clip at split-ish
composite(-40, 18, 19.2, "dx40_dy18_waist")
composite(-40, 28.5, 19.2, "dx40_dy28_waist")
composite(-55, 28.5, 19.2, "dx55_dy28_waist")
composite(-32, 12, 19.2, "dx32_dy12_waist")
print("done")
