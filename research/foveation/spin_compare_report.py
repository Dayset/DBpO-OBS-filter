"""PSNR / SSIM (luma) of baseline and pipeline frames against the original, split into sharp center and periphery.

Usage: python3 spin_compare_report.py OUT
"""
import glob
import os
import re
import sys

import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity

CX, CY = 0.15, 0.15  # sharp center half-size (fractions), as in the config


def luma(path):
    a = np.asarray(Image.open(path).convert("RGB"), np.float64)
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def metrics(ref, test, mask):
    mse = ((ref - test)[mask] ** 2).mean()
    ps = 99.0 if mse < 1e-9 else 10 * np.log10(255 ** 2 / mse)
    _, smap = structural_similarity(ref, test, data_range=255, full=True)
    return ps, smap[mask].mean()


def main(out):
    ids = sorted(int(re.search(r"frame_(\d+)_original", p).group(1)) for p in glob.glob(os.path.join(out, "frame_*_original.png")))
    rows = []
    for i in ids:
        ref = luma(os.path.join(out, f"frame_{i:03d}_original.png"))
        h, w = ref.shape
        y, x = (np.arange(h) + 0.5) / h, (np.arange(w) + 0.5) / w
        center = (np.abs(y[:, None] - 0.5) < CY) & (np.abs(x[None, :] - 0.5) < CX * 1.0 + 0.0)
        center = (np.abs(y[:, None] - 0.5) < CY) & (np.abs(x[None, :] - 0.5) < CX)
        for name, path in (("baseline", f"frame_{i:03d}_baseline.png"), ("unsquish only", f"extras/frame_{i:03d}_unsquish_only.png"),
                           ("pipeline", f"frame_{i:03d}_pipeline.png")):
            test = luma(os.path.join(out, path))
            c = metrics(ref, test, center)
            p = metrics(ref, test, ~center)
            rows.append((i, name, *c, *p))
    names = ["baseline", "unsquish only", "pipeline"]
    lines = [f"{'variant':<15}{'center PSNR':>12}{'center SSIM':>12}{'periph PSNR':>12}{'periph SSIM':>12}"]
    for n in names:
        r = np.array([x[2:] for x in rows if x[1] == n])
        lines.append(f"{n:<15}{r[:, 0].mean():12.2f}{r[:, 1].mean():12.4f}{r[:, 2].mean():12.2f}{r[:, 3].mean():12.4f}")
    text = "\n".join(lines)
    print(text)
    open(os.path.join(out, "metrics.txt"), "w").write(
        "Mean over the 10 frames, luma, vs the 1440p original. center = middle 30% x 30%, periphery = the rest.\n" + text + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
