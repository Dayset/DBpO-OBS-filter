"""Viewer-side restoration after the Unsquish unwarp: does anything beat plain bilinear?

Same squish as foveation_bench (X+Y 75%/75%, CBR like a stream). Encode once per clip, then compare different
decoder-side reconstructions of the SAME decoded frames against the original (luma PSNR center/edge + edge SSIM).

Usage: python3 viewer_restoration.py [frames=10]
Indicative only (synthetic clips, few frames, one I-frame in the GOP): re-measure on real footage.
"""

import os
import sys

import numpy as np
from scipy.ndimage import gaussian_filter
from skimage.metrics import structural_similarity

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foveation_bench as fb  # noqa: E402

h, H, W = fb.h, fb.H, fb.W


def lanczos_matrix(pos, n_in, a=3):
    """(n_out, n_in) matrix sampling n_in source pixels at fractional pixel-center positions `pos`."""
    p = pos - 0.5
    cols = np.arange(n_in)
    x = p[:, None] - cols[None, :]
    m = np.sinc(x) * np.sinc(x / a) * (np.abs(x) < a)
    return (m / m.sum(1, keepdims=True)).astype(np.float32)


def unwarp(img, ux, vy, kernel):
    """Squished (rows', cols', 3) -> full size, along X then Y."""
    if kernel == "bilinear":
        t = fb.resample_cols(img, ux)
        return fb.resample_cols(t.transpose(1, 0, 2), vy).transpose(1, 0, 2)
    mx = lanczos_matrix(ux, img.shape[1])
    t = np.einsum("oc,rcz->roz", mx, img)
    my = lanczos_matrix(vy, img.shape[0])
    return np.einsum("or,rcz->ocz", my, t)


def main(n):
    d, x_of_u, u_centers = fb.warp_tables(720)
    fb.W = H
    d_y, y_of_v, v_centers = fb.warp_tables(406)
    fb.W = W
    weight = np.maximum((1 - d)[None, :], (1 - d_y)[:, None])
    weight = (weight / weight.max())[..., None].astype(np.float32)
    x = (np.arange(W) + 0.5) / W
    edge_mask = np.abs(x - 0.5) >= fb.CENTER
    flat = h.Filter(None, "off", flatten=0.012)

    def sharpen(img, amt):
        return img + amt * weight * (img - gaussian_filter(img, (1.0, 1.0, 0)))

    def grain(img, sigma, seed=3):
        rng = np.random.default_rng(seed)
        return img + rng.normal(0, sigma, img.shape).astype(np.float32) * weight

    recon = {
        "bilinear (current Unsquish)": lambda f, i: unwarp(f, u_centers, v_centers, "bilinear"),
        "lanczos3 unwarp": lambda f, i: unwarp(f, u_centers, v_centers, "lanczos"),
        "bilinear + sharpen 0.8": lambda f, i: sharpen(unwarp(f, u_centers, v_centers, "bilinear"), 0.8),
        "lanczos3 + sharpen 0.5": lambda f, i: sharpen(unwarp(f, u_centers, v_centers, "lanczos"), 0.5),
        "deblock (edge-preserving 0.012) + lanczos3": lambda f, i: unwarp(flat(f), u_centers, v_centers, "lanczos"),
        "lanczos3 + grain 2/255 (periphery)": lambda f, i: grain(unwarp(f, u_centers, v_centers, "lanczos"), 2 / 255),
        "deblock + lanczos3 + sharpen 0.5": lambda f, i: sharpen(unwarp(flat(f), u_centers, v_centers, "lanczos"), 0.5),
    }

    for clip, kbps in (("fps_pan_clean", 250), ("fps_pan_grain", 300)):
        src = list(h.clip_frames(clip, n))
        enc = []
        for f in src:
            g = fb.resample_cols(fb.preblur(f, d), x_of_u).transpose(1, 0, 2)
            g = fb.resample_cols(fb.preblur_rows(g, d_y), y_of_v).transpose(1, 0, 2)
            enc.append(np.ascontiguousarray(g))
        path = os.path.join(fb.TMP, "r.mp4")
        rate = fb.encode(enc, 60, 0, path, cbr_kbps=kbps)
        dec = fb.decode(path, enc[0].shape[1], enc[0].shape[0])
        print(f"\n== {clip}: squish X+Y 75/75, CBR {kbps} kbps (actual {rate:.0f}), {n} frames")
        print(f"   {'viewer reconstruction':<46}{'center':>8}{'edge':>8}{'edge SSIM':>10}")
        for name, fn in recon.items():
            out = [np.clip(fn(f, i), 0, 1) for i, f in enumerate(dec)]
            pc, pe = fb.psnr_regions(src, out)
            ss = np.mean([structural_similarity(fb.luma(a), fb.luma(b), data_range=1, full=True)[1][:, edge_mask].mean()
                          for a, b in zip(src, out)])
            print(f"   {name:<46}{pc:8.2f}{pe:8.2f}{ss:10.4f}", flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 10)
