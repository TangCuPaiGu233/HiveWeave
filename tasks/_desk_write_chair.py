"""Write production desk sprites: rear chair in BACK, desktop wedge in FRONT."""
from pathlib import Path
import shutil
from PIL import Image

ASSETS = Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets")
OUT = Path(r"D:\PC_AI\Project\HiveWeave\tasks\_desk_debug")

for name, src in (
    ("office-desk-back.png", OUT / "new_back_chair.png"),
    ("office-desk-front.png", OUT / "new_front_nowedge.png"),
):
    dest = ASSETS / name
    bak = ASSETS / name.replace(".png", ".pre-chair.bak.png")
    if dest.exists() and not bak.exists():
        shutil.copy2(dest, bak)
        print("backed up", bak.name)
    shutil.copy2(src, dest)
    im = Image.open(dest)
    print("wrote", name, im.size)
