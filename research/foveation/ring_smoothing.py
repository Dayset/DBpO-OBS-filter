"""Smoothing the middle ring (between the sharp center and the squeezed edges).

Does pre-smoothing the ring (not the center, not the outer edges the squish
already averages) free bits for the center at a fixed bitrate, and which
smoother keeps the most structure for the bits it frees?

Smoothers, all applied only in the ring (smooth weight mask):
  gaussian      sigma 1.5, linear
  radon30/60    filtered backprojection from 30/60 angles (the "Radon" idea), linear
  bilateral     edge-preserving (OpenCV), nonlinear
  wavelet       BayesShrink soft thresholding (skimage), nonlinear

Pipeline: smooth -> Foveated Squish 75% (exact area filter) -> x264 CBR ->
unsquish. Reported: center PSNR (where the freed bits go), ring SSIM vs the
original (structure kept), and the smoother's own ring SSIM before encoding.

Usage: python3 research/foveation/ring_smoothing.py [frames] [kbps]
"""
import os
import sys
import time

import cv2
import numpy as np
from skimage.metrics import structural_similarity
from skimage.restoration import denoise_wavelet
from skimage.transform import iradon, radon

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import foveation_bench as fb  # noqa: E402
import squish_profiles as sp  # noqa: E402
from squish_math import Axis  # noqa: E402

H, W = fb.H, fb.W


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


Y, X = np.mgrid[0:H, 0:W]
M = np.maximum(np.abs((X + 0.5) / W - 0.5), np.abs((Y + 0.5) / H - 0.5))
RING_W = (smoothstep(0.15, 0.2, M) * (1 - smoothstep(0.35, 0.45, M)))[..., None]
FOVEA = M < 0.15
RING = (M > 0.2) & (M < 0.35)


def radon_smooth(img, n_angles):
    s = max(H, W)
    out = np.empty_like(img)
    theta = np.linspace(0.0, 180.0, n_angles, endpoint=False)
    for ch in range(3):
        sq = np.zeros((s, s))
        oy, ox = (s - H) // 2, (s - W) // 2
        sq[oy:oy + H, ox:ox + W] = img[..., ch]
        rec = iradon(radon(sq, theta=theta, circle=False), theta=theta, filter_name="ramp", circle=False,
                     output_size=s)
        out[..., ch] = rec[oy:oy + H, ox:ox + W]
    return np.clip(out, 0, 1)


SMOOTHERS = {
    "none": None,
    "gaussian": lambda f: cv2.GaussianBlur(f, (0, 0), 1.5),
    "radon30": lambda f: radon_smooth(f, 30),
    "radon60": lambda f: radon_smooth(f, 60),
    "bilateral": lambda f: cv2.bilateralFilter(f.astype(np.float32), 7, 0.08, 3),
    "wavelet": lambda f: np.clip(denoise_wavelet(f, channel_axis=-1, method="BayesShrink", mode="soft",
                                                 rescale_sigma=True), 0, 1),
}


def ring_ssim(a_frames, b_frames):
    vals = []
    for a, b in zip(a_frames, b_frames):
        _, full = structural_similarity(fb.luma(a), fb.luma(b), data_range=1.0, full=True)
        vals.append(full[RING].mean())
    return float(np.mean(vals))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    kbps = int(sys.argv[2]) if len(sys.argv) > 2 else 250
    ax, ay = Axis(r=0.75, c=0.15, p=1.0), Axis(r=0.75, c=0.15, p=1.0)
    mw, mh = 720, 404
    ax.r, ay.r = mw / W, mh / H
    Ax, Ay = sp.area_matrix(ax, W, mw), sp.area_matrix(ay, H, mh)
    Bx, By = sp.bilinear_matrix(ax, W, mw), sp.bilinear_matrix(ay, H, mh)
    for clip in ("fps_pan_clean", "fps_pan_grain"):
        src = [f.astype(np.float32) for f in fb.h.clip_frames(clip, n)]
        print(f"\n== {clip}, squish 75% x 75%, CBR {kbps} kbps, {n} frames")
        print(f"   {'ring smoother':<12}{'center PSNR':>12}{'ring SSIM':>11}{'ring SSIM before encode':>25}{'time/frame':>12}")
        for name, fn in SMOOTHERS.items():
            t0 = time.time()
            if fn is None:
                pre = src
            else:
                pre = [RING_W * fn(f) + (1 - RING_W) * f for f in src]
            dt = (time.time() - t0) / n
            path = os.path.join(fb.TMP, "r.mp4")
            fb.encode([np.ascontiguousarray(sp.warp2(Ay, f, Ax)) for f in pre], 60, 0, path, cbr_kbps=kbps)
            out = [sp.warp2(By, d, Bx) for d in fb.decode(path, mw, mh)]
            print(f"   {name:<12}{sp.psnr(src, out, FOVEA):12.2f}{ring_ssim(src, out):11.3f}"
                  f"{ring_ssim(src, pre) if fn else 1.0:25.3f}{dt * 1000:10.0f}ms", flush=True)


if __name__ == "__main__":
    main()
