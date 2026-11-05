"""Viewer-side "second wind" for soft unsquished edges: edge steepening + flat merge + dither + vibrance.

Not fidelity-oriented: measures edge crispness (gradient strength / 10-90% edge width in the periphery) next to PSNR,
and writes a side-by-side crop (original | bilinear | lanczos+sharpen | snap) for eyeballing.
Usage: python3 viewer_snap_preview.py OUT.png [frames=10]
"""

import os
import sys

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, maximum_filter, median_filter, minimum_filter, sobel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foveation_bench as fb  # noqa: E402
import viewer_restoration as vr  # noqa: E402

h, H, W = fb.h, fb.H, fb.W


def shock(img, iters=2, strength=1.0):
    """Edge steepening: pull pixels on a soft edge toward the local min or max (sign of luma Laplacian)."""
    for _ in range(iters):
        y = fb.luma(gaussian_filter(img, (0.7, 0.7, 0)))
        lap = gaussian_filter(y, 0.7, order=(2, 0)) + gaussian_filter(y, 0.7, order=(0, 2))
        lo = np.stack([minimum_filter(img[..., c], 3) for c in range(3)], -1)
        hi = np.stack([maximum_filter(img[..., c], 3) for c in range(3)], -1)
        target = np.where((lap > 0)[..., None], lo, hi)  # convex side -> darker, concave -> brighter
        g = np.hypot(sobel(y, 0), sobel(y, 1))
        a = np.clip(g * 6, 0, 1)[..., None] * strength
        img = img * (1 - a * 0.5) + target * a * 0.5
    return img


def vibrance(img, amt):
    y = fb.luma(img)[..., None]
    sat = img.max(-1, keepdims=True) - img.min(-1, keepdims=True)
    return y + (img - y) * (1 + amt * (1 - sat))  # low-saturation pixels gain most


def snap(img, weight, dither=0.5, vib=0.25):
    sharp = shock(img)
    y = fb.luma(img)
    edges = np.clip(np.hypot(sobel(y, 0), sobel(y, 1)) * 4, 0, 1)
    med = np.stack([median_filter(sharp[..., c], 3) for c in range(3)], -1)
    out = np.where((edges < 0.15)[..., None], med, sharp)  # flat areas merge, edges stay hard
    out = vibrance(out, vib)
    m = np.tile(h.BAYER4, (H // 4 + 1, W // 4 + 1))[:H, :W, None]  # static ordered dither, free on the viewer
    out = np.round((out + m * dither / 16) * 31) / 31  # 5-bit levels + dither
    return img * (1 - weight) + out * weight  # only in the squeezed periphery


def crispness(f, mask):
    g = np.hypot(sobel(fb.luma(f), 0), sobel(fb.luma(f), 1))[:, mask]
    return np.percentile(g, 99), g.mean()


def main(path, n):
    d, x_of_u, u_centers = fb.warp_tables(720)
    fb.W = H
    d_y, y_of_v, v_centers = fb.warp_tables(406)
    fb.W = W
    weight = np.maximum((1 - d)[None, :], (1 - d_y)[:, None])
    weight = (np.clip(weight / weight.max() * 1.5, 0, 1))[..., None].astype(np.float32)
    mask = np.abs((np.arange(W) + 0.5) / W - 0.5) >= fb.CENTER
    src = list(h.clip_frames("fps_pan_clean", n))
    enc = []
    for f in src:
        g = fb.resample_cols(fb.preblur(f, d), x_of_u).transpose(1, 0, 2)
        g = fb.resample_cols(fb.preblur_rows(g, d_y), y_of_v).transpose(1, 0, 2)
        enc.append(np.ascontiguousarray(g))
    rate = fb.encode(enc, 60, 0, os.path.join(fb.TMP, "s.mp4"), cbr_kbps=250)
    dec = fb.decode(os.path.join(fb.TMP, "s.mp4"), enc[0].shape[1], enc[0].shape[0])

    def bil(f):
        return vr.unwarp(f, u_centers, v_centers, "bilinear")

    def lz(f):
        return vr.unwarp(f, u_centers, v_centers, "lanczos")

    def lzs(f):
        im = lz(f)
        return im + 0.5 * weight * (im - gaussian_filter(im, (1.0, 1.0, 0)))

    methods = {"bilinear (current)": bil, "lanczos3 + sharpen": lzs, "snap (shock+merge+dither+vibrance)":
               lambda f: snap(lz(f), weight)}
    outs = {k: [np.clip(fn(f), 0, 1) for f in dec] for k, fn in methods.items()}
    print(f"squish X+Y 75/75 at 250 kbps (actual {rate:.0f}), {n} frames, edge columns only")
    o99 = np.mean([crispness(f, mask)[0] for f in src])
    print(f"   {'':<38}{'edge PSNR':>10}{'grad p99':>9}{'vs original':>12}")
    print(f"   {'original':<38}{'-':>10}{o99:9.3f}{'1.00x':>12}")
    for k, v in outs.items():
        _, pe = fb.psnr_regions(src, v)
        c99 = np.mean([crispness(f, mask)[0] for f in v])
        print(f"   {k:<38}{pe:10.2f}{c99:9.3f}{c99 / o99:11.2f}x")

    i = n - 2
    ys, xs = slice(250, 400), slice(0, 220)  # left periphery crop
    tiles = [src[i]] + [v[i] for v in outs.values()]
    strip = np.concatenate([np.kron(t[ys, xs], np.ones((3, 3, 1))) for t in tiles], 1)
    Image.fromarray((np.clip(strip, 0, 1) * 255 + 0.5).astype(np.uint8)).save(path)
    print("crop order: original | " + " | ".join(outs))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 10)
