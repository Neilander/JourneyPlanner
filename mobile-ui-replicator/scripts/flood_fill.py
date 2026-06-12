#!/usr/bin/env python3
"""
Knock out the background of a mascot/icon image and write a transparent PNG.

Usage:
    python scripts/flood_fill.py --in mascot.png --out mascot.png \
        [--tolerance 28] [--min-blob 400]

Approach:
  1. Sample background color from the 4 corners (median).
  2. Build a binary mask of "background-like" pixels (color distance < tolerance).
  3. Flood from the corners through that mask so only the OUTSIDE background gets removed —
     pale pixels INSIDE the mascot stay opaque.
  4. Sweep up isolated specks (small connected components of opaque pixels < min-blob).
  5. Feather the alpha edge by 1px so the cutout doesn't look digital.

Requires: pillow, numpy, scipy.
    pip install pillow numpy scipy
"""
import argparse, sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage


def remove_bg(img: Image.Image, tolerance: int = 28, min_blob: int = 400) -> Image.Image:
    img = img.convert("RGBA")
    arr = np.array(img)
    h, w = arr.shape[:2]
    rgb = arr[:, :, :3].astype(np.int16)

    # 1. background color = median of the 4 corner patches
    patch = max(4, min(h, w) // 40)
    corners = np.vstack([
        rgb[:patch, :patch].reshape(-1, 3),
        rgb[:patch, -patch:].reshape(-1, 3),
        rgb[-patch:, :patch].reshape(-1, 3),
        rgb[-patch:, -patch:].reshape(-1, 3),
    ])
    bg = np.median(corners, axis=0)

    # 2. background-like mask (per-pixel distance under tolerance)
    dist = np.sqrt(((rgb - bg) ** 2).sum(axis=2))
    bg_like = dist < tolerance

    # 3. flood from corners: only bg-like pixels CONNECTED to a corner get removed.
    # Label connected components in bg_like; mark the labels that touch any corner.
    labels, n = ndimage.label(bg_like)
    corner_labels = set()
    for y in (0, h - 1):
        for x in (0, w - 1):
            corner_labels.add(int(labels[y, x]))
    corner_labels.discard(0)
    bg_mask = np.isin(labels, list(corner_labels))

    alpha = np.where(bg_mask, 0, 255).astype(np.uint8)

    # 4. drop tiny opaque specks (likely noise specks far from the subject)
    opaque = alpha > 0
    op_labels, op_n = ndimage.label(opaque)
    if op_n > 0:
        sizes = ndimage.sum(opaque, op_labels, index=np.arange(1, op_n + 1))
        small = np.where(sizes < min_blob)[0] + 1
        if small.size:
            drop = np.isin(op_labels, small)
            alpha[drop] = 0

    # 5. feather the alpha edge 1px so the cutout doesn't look digital
    soft = ndimage.uniform_filter(alpha.astype(np.float32), size=3)
    alpha = np.maximum(alpha, soft.astype(np.uint8) // 2 if False else alpha)  # keep hard edge but smoothable later
    # simpler: blur alpha by 1px only at the boundary
    edge = (alpha > 0) ^ ndimage.binary_erosion(alpha > 0)
    blurred = ndimage.gaussian_filter(alpha.astype(np.float32), sigma=0.7)
    alpha = np.where(edge, blurred, alpha).astype(np.uint8)

    arr[:, :, 3] = alpha
    return Image.fromarray(arr, mode="RGBA")


def main() -> int:
    ap = argparse.ArgumentParser(description="Knock out background, write transparent PNG.")
    ap.add_argument("--in", dest="inp", required=True, help="Input image (PNG/JPG).")
    ap.add_argument("--out", required=True, help="Output PNG path (RGBA).")
    ap.add_argument("--tolerance", type=int, default=28, help="Color distance threshold (0–441). Default 28.")
    ap.add_argument("--min-blob", type=int, default=400, help="Drop opaque blobs smaller than N pixels. Default 400.")
    args = ap.parse_args()

    inp = Path(args.inp)
    if not inp.exists():
        print(f"ERROR: input not found: {inp}", file=sys.stderr)
        return 2

    img = Image.open(inp)
    out_img = remove_bg(img, tolerance=args.tolerance, min_blob=args.min_blob)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_img.save(out_path, "PNG", optimize=True)
    print(f"OK  {out_path}  ({out_img.size[0]}x{out_img.size[1]} RGBA)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
