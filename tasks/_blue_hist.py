from pathlib import Path
from PIL import Image
import numpy as np
full = np.array(Image.open(Path(r"D:\PC_AI\Project\HiveWeave\apps\web\public\office-assets\office-desk-set.png")).convert("RGBA"))
H,W = full.shape[:2]
r,g,b,a = full[:,:,0].astype(int), full[:,:,1].astype(int), full[:,:,2].astype(int), full[:,:,3]
blue = (a>8) & (b>90) & (b>r+8) & (b>g-10)
ys, xs = np.where(blue)
print("blue count", len(ys), "x", xs.min(), xs.max(), "y", ys.min(), ys.max())
# histogram of y
for y0 in range(0, H, 10):
    n = int(blue[y0:y0+10].sum())
    if n:
        print(f"  y {y0:3d}-{y0+9:3d}: {n:4d}  x={xs[(ys>=y0)&(ys<y0+10)].min() if n else '-'}..{xs[(ys>=y0)&(ys<y0+10)].max() if n else '-'}")
