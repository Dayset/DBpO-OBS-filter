"""Checks the "256 colors + 0.3 temporal dither saves 25-45%" claim on game footage.

Per frame: k-means palette in OKLab-like space (Lab via OpenCV), 256 / 128 /
512 colors, optional ordered (Bayer 4x4) dither at strength s whose pattern
shifts every 4 frames. Encoded with x264 veryfast at CRF 23 (and CBR for the
quality side). Reports bitrate vs the original and luma PSNR vs the original.

Usage: python3 research/foveation/palette256.py [frames]
"""
import os
import sys

import cv2
import numpy as np
from scipy.spatial import cKDTree

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import foveation_bench as fb  # noqa: E402

BAYER = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], np.float32) / 16.0 - 0.5


def quantize(frame, k, strength, idx, rng):
    lab = cv2.cvtColor(frame.astype(np.float32), cv2.COLOR_RGB2Lab)
    h, w, _ = lab.shape
    pts = lab.reshape(-1, 3)
    sample = pts[rng.choice(len(pts), 20000, replace=False)]
    _, _, centers = cv2.kmeans(sample, k, None, (cv2.TERM_CRITERIA_MAX_ITER + cv2.TERM_CRITERIA_EPS, 10, 0.5), 1,
                               cv2.KMEANS_PP_CENTERS)
    if strength > 0:  # ordered dither, pattern shifted every 4 frames, amplitude ~ palette spacing
        shift = (idx // 4) % 4
        pat = np.roll(np.tile(BAYER, (h // 4 + 1, w // 4 + 1))[:h, :w], shift, axis=1)
        spacing = 100.0 / k ** (1 / 3)  # rough L*a*b* step between palette colors
        lab = lab + (strength * spacing * pat)[..., None]
        pts = lab.reshape(-1, 3)
    labels = cKDTree(centers).query(pts)[1]  # nearest palette color
    out = centers[labels].reshape(h, w, 3)
    return np.clip(cv2.cvtColor(out.astype(np.float32), cv2.COLOR_Lab2RGB), 0, 1)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    rng = np.random.default_rng(1)
    variants = [("original", None, 0), ("512 colors, dither 0.2", 512, 0.2), ("256 colors, no dither", 256, 0.0),
                ("256 colors, dither 0.3", 256, 0.3), ("256 colors, dither 0.5", 256, 0.5),
                ("128 colors, dither 0.4", 128, 0.4)]
    for clip in ("fps_pan_clean", "fps_pan_grain"):
        src = [f.astype(np.float32) for f in fb.h.clip_frames(clip, n)]
        print(f"\n== {clip}, x264 veryfast CRF 23, {n} frames")
        base = None
        for name, k, s in variants:
            frames = src if k is None else [quantize(f, k, s, i, rng) for i, f in enumerate(src)]
            path = os.path.join(fb.TMP, "q.mp4")
            kbps = fb.encode(frames, 60, 23, path)
            dec = fb.decode(path, fb.W, fb.H)
            mse = np.mean([np.mean((fb.luma(a) - fb.luma(b)) ** 2) for a, b in zip(src, dec)])
            base = base or kbps
            print(f"   {name:<26}{kbps:8.0f} kbps {100 * (kbps / base - 1):+7.1f}%   PSNR vs original "
                  f"{10 * np.log10(1 / mse):6.2f} dB", flush=True)


if __name__ == "__main__":
    main()
