"""Sanity-check: BACK+FRONT should reconstruct the desk (minus rear chair punch)."""
from pathlib import Path
from PIL import Image
import numpy as np

OUT = Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug")
ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
SPLIT = 54

full = np.array(Image.open(ASSETS / "office-desk-set.png").convert("RGBA"))
back = np.array(Image.open(OUT / "new_back.png").convert("RGBA"))
front = np.array(Image.open(OUT / "new_front.png").convert("RGBA"))

recon = np.zeros_like(full)
recon[: back.shape[0], : back.shape[1]] = back
# alpha composite front at SPLIT
fr = Image.fromarray(recon)
fr.paste(Image.fromarray(front), (0, SPLIT), Image.fromarray(front))
fr.save(OUT / "recon_back_front.png")

# diff vs original (ignore rear-chair region x<97, y<54)
arr = np.array(fr)
orig = full.copy()
# compare opaque
diff = np.abs(arr.astype(int) - orig.astype(int)).sum(axis=2)
# mask out known chair punch
ignore = np.zeros(full.shape[:2], dtype=bool)
ignore[:SPLIT, :97] = True
show = (diff > 30) & ~ignore
print("diff px outside chair punch", int(show.sum()), "of", int((~ignore).sum()))
vis = orig.copy()
vis[show] = (255, 0, 0, 255)
Image.fromarray(vis).save(OUT / "recon_diff.png")

# nicer sitting preview on wood floor
wood = (210, 160, 105, 255)
purple = Image.open(ASSETS / "agent-purple-typing-sheet.png").convert("RGBA")
PX = 208 / 159.2
disp = int(round(96 * 0.8 * PX))
char = purple.crop((0, 0, 96, 96)).resize((disp, disp), Image.Resampling.NEAREST)
cx = (-40 + 78.2) * PX
cy = (8 + 62.1) * PX
paste = (int(round(cx - 0.5 * disp)), int(round(cy - 0.875 * disp)))
bg = Image.new("RGBA", (208, 170), wood)
bg.paste(Image.fromarray(back), (0, 0), Image.fromarray(back))
bg.paste(char, paste, char)
bg.paste(Image.fromarray(front), (0, SPLIT), Image.fromarray(front))
bg.save(OUT / "preview_final_wood.png")
print("paste", paste, "done")
