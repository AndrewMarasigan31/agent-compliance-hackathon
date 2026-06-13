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
STAMP_WIDTH_RATIO = 0.122          # stamp width as fraction of photo width
RIGHT_MARGIN_RATIO = 0.046         # right margin as fraction of photo width
BOTTOM_MARGIN_RATIO = 0.035        # bottom margin as fraction of photo height
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
    return stamp


if __name__ == "__main__":
    stamp = extract_stamp()
    print(f"Stamp extracted: {stamp.size[0]}x{stamp.size[1]}px, "
          f"{np.sum(np.array(stamp)[:,:,3] > 0)} opaque pixels")
