#!/usr/bin/env python3
"""
Extract tab rows from each page and stitch into one horizontal strip.
Each page is landscape with rows of tab. We detect rows by finding the
white vertical gaps between systems, then crop and stitch horizontally.
"""
from PIL import Image
import os
import numpy as np

PAGES_DIR = "jarabi_pages"
OUT_FILE = "jarabi_strip.png"
WHITE_THRESH = 240   # mean brightness to call a row "white"
MIN_ROW_H = 80       # a real tab row is at least this many px tall
ROW_PAD = 8          # extra px above/below each extracted row

def get_content_bands(img):
    arr = np.array(img.convert("L"))
    h = arr.shape[0]

    # For each scanline, is it mostly white?
    white = np.array([arr[y].mean() > WHITE_THRESH for y in range(h)])

    # Find content spans (where not white)
    in_content = False
    raw_bands = []
    start = 0
    for y in range(h):
        if not in_content and not white[y]:
            in_content = True
            start = y
        elif in_content and white[y]:
            raw_bands.append((start, y))
            in_content = False
    if in_content:
        raw_bands.append((start, h))

    # Merge adjacent bands that are close — handles notation above staff
    # being detected as a separate thin band
    merged = []
    for b in raw_bands:
        if merged:
            gap = b[0] - merged[-1][1]
            if gap < 40:  # merge if gap < 40px
                merged[-1] = (merged[-1][0], b[1])
                continue
        merged.append(list(b))

    # Only keep bands tall enough to be a real tab row
    rows = [(max(0, y0 - ROW_PAD), min(h, y1 + ROW_PAD))
            for y0, y1 in merged if (y1 - y0) >= MIN_ROW_H]
    return rows


pages = sorted([f for f in os.listdir(PAGES_DIR) if f.endswith(".png")])
print(f"Processing {len(pages)} pages...")

all_rows = []

for page_file in pages:
    path = os.path.join(PAGES_DIR, page_file)
    img = Image.open(path)
    bands = get_content_bands(img)
    heights = [y1-y0 for y0,y1 in bands]
    print(f"  {page_file}: {len(bands)} rows, heights={heights}")
    for (y0, y1) in bands:
        row_img = img.crop((0, y0, img.width, y1))
        all_rows.append(row_img)

print(f"\nTotal rows: {len(all_rows)}")

# Scale all rows to the same height
heights = [r.height for r in all_rows]
target_h = int(np.median(heights))
print(f"Target height: {target_h}px")

normalized = []
for r in all_rows:
    if r.height != target_h:
        scale = target_h / r.height
        new_w = int(r.width * scale)
        r = r.resize((new_w, target_h), Image.LANCZOS)
    normalized.append(r)

# Stitch horizontally with a small white separator
SEP = 30
total_w = sum(r.width for r in normalized) + SEP * (len(normalized) - 1)
strip = Image.new("RGB", (total_w, target_h), (255, 255, 255))
x = 0
for i, r in enumerate(normalized):
    strip.paste(r, (x, 0))
    x += r.width + SEP

strip.save(OUT_FILE, optimize=True)
print(f"Saved: {OUT_FILE}  ({total_w} x {target_h} px)")
