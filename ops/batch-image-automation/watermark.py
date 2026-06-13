#!/usr/bin/env python3
"""Batch watermark: extract stamp, apply to all images, re-zip."""

import zipfile
import io
import os
from pathlib import Path
import numpy as np
from PIL import Image

BASE = Path(__file__).parent
TEMPLATE_PNG = BASE / "EXCEL VIGNETTE WATERMARKED BATCH 3.png"
STAMP_PNG = BASE / "mega_stamp.png"
SOURCE_ZIP = BASE / "SELECTS-20260613T071730Z-3-003.zip"
OUTPUT_ZIP = BASE / "SELECTS-WATERMARKED-20260613.zip"

# Geometry constants derived from template analysis
STAMP_Y1, STAMP_Y2 = 1240, 1310   # +/-5px padding around detected region 1247-1303
STAMP_X1, STAMP_X2 = 888, 1040    # +/-10px padding around detected region 898-1030
STAMP_WIDTH_RATIO = 0.1407         # stamp width as fraction of photo width (152/1080)
RIGHT_MARGIN_RATIO = 0.0370        # right margin as fraction of photo width (40/1080)
BOTTOM_MARGIN_RATIO = 0.0296       # bottom margin as fraction of photo height (40/1350)
JPEG_QUALITY = 95


def extract_stamp() -> Image.Image:
    """Extract white MEGA text from template PNG as transparent RGBA stamp."""
    template = Image.open(TEMPLATE_PNG)
    arr = np.array(template)

    crop = arr[STAMP_Y1:STAMP_Y2, STAMP_X1:STAMP_X2]
    h, w = crop.shape[:2]

    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    white_mask = np.all(crop > 200, axis=2)
    rgba[white_mask] = [255, 255, 255, 255]

    stamp = Image.fromarray(rgba)
    stamp.save(STAMP_PNG)
    print(f"Saved stamp to {STAMP_PNG}")
    return stamp


def apply_watermark(photo: Image.Image, stamp: Image.Image) -> Image.Image:
    """Composite stamp onto photo at bottom-right, scaled proportionally."""
    photo = photo.convert("RGB")
    pw, ph = photo.size

    # Scale stamp to match photo width ratio
    target_w = int(pw * STAMP_WIDTH_RATIO)
    sw, sh = stamp.size
    target_h = int(target_w * sh / sw)
    scaled = stamp.resize((target_w, target_h), Image.LANCZOS)

    # Bottom-right placement
    x = pw - target_w - int(pw * RIGHT_MARGIN_RATIO)
    y = ph - target_h - int(ph * BOTTOM_MARGIN_RATIO)

    result = photo.copy()
    result.paste(scaled, (x, y), mask=scaled.split()[3])
    return result


if __name__ == "__main__":
    import sys

    stamp = extract_stamp()
    print(f"Stamp: {stamp.size}, opaque={np.sum(np.array(stamp)[:,:,3] > 0)} px")

    mode = sys.argv[1] if len(sys.argv) > 1 else "test"

    if mode == "test":
        # Test on up to 3 images from zip (try to get at least one landscape)
        tested = 0
        with zipfile.ZipFile(SOURCE_ZIP) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".jpg")]
            for name in names[:5]:
                with zf.open(name) as f:
                    photo = Image.open(f)
                    photo.load()
                w, h = photo.size
                orientation = "landscape" if w > h else "portrait"
                result = apply_watermark(photo, stamp)
                out_name = f"test_output_{orientation}_{Path(name).name}"
                result.save(BASE / out_name, "JPEG", quality=JPEG_QUALITY)
                print(f"  {name} ({w}x{h}, {orientation}) → {out_name}")
                tested += 1
                if tested >= 3:
                    break
        print(f"Test complete. Check {BASE}/ for test_output_*.jpg files.")

    elif mode == "run":
        process_all(stamp)
