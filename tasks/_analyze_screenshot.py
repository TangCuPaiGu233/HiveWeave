"""Locate desks vs characters in the user's screenshot."""
from pathlib import Path
from PIL import Image
import numpy as np

p = Path(r"C:\Users\99744\.cursor\projects\d-PC-AI-Project-HiveWeave\assets\c__Users_99744_AppData_Roaming_Cursor_User_workspaceStorage_b4f52de9dd61d205b6da24cf6cc6f4fa_images_image-3f93e1f9-6179-4559-a3bd-2395cfc0b8dc.jpg")
im = Image.open(p)
arr = np.array(im)
print("screenshot", im.size, arr.shape, im.mode)

# Purple jacket characters: high R-B? purple jacket ~ (120, 70, 160) or similar
# Let's find pixels that look like the purple character
r, g, b = arr[:, :, 0].astype(int), arr[:, :, 1].astype(int), arr[:, :, 2].astype(int)
# purple-ish: R and B higher than G, not too dark
purple = (r > 80) & (b > 80) & (g < r - 10) & (g < b - 5) & (r > g) & (b > 70)
print("purple-ish px", int(purple.sum()))

# Connected-component-ish: row/col sums to find blobs
rows = np.where(purple.any(axis=1))[0]
cols = np.where(purple.any(axis=0))[0]
print("purple bbox y", rows[0] if len(rows) else None, rows[-1] if len(rows) else None,
      "x", cols[0] if len(cols) else None, cols[-1] if len(cols) else None)

# Find vertical strips of purple (character columns)
col_counts = purple.sum(axis=0)
# peaks
from scipy.ndimage import label
# fallback without scipy: simple run-length on columns with many purple
active = col_counts > 8
starts = []
in_run = False
s = 0
for i, v in enumerate(active):
    if v and not in_run:
        s = i; in_run = True
    elif not v and in_run:
        if i - s > 8:
            starts.append((s, i, int(col_counts[s:i].max()), int(np.argmax(col_counts[s:i]) + s)))
        in_run = False
print("purple column runs (x0,x1,max,peak):")
for t in starts:
    print(" ", t)

# White partition: very light pixels
white = (r > 200) & (g > 200) & (b > 200)
print("white px", int(white.sum()))
