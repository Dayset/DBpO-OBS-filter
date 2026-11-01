"""Which squish curve is best for a fixed stream bitrate (CBR)?

Uses squish_math.py (the plugin's exact mapping) with exact area-filtered
warping (what the shader approximates) and bilinear unwarping (what the
browser extension does). Reports luma PSNR in the fovea (center box), a middle
ring and the outer region.

Usage: python3 research/foveation/squish_profiles.py [frames]
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import foveation_bench as fb  # noqa: E402
from squish_math import Axis  # noqa: E402

H, W = fb.H, fb.W


def warp2(rows, img, cols):
    """Separable resample: rows (out_h x in_h) @ img @ cols.T (out_w x in_w)."""
    return np.einsum("vx,uxc->uvc", cols, np.einsum("uy,yxc->uxc", rows, img, optimize=True), optimize=True)


def area_matrix(axis, n_src, n_out):
    """Row u averages the source interval that maps to output pixel u."""
    edges = axis.inverse(np.arange(n_out + 1) / n_out) * n_src  # source px
    A = np.zeros((n_out, n_src))
    for u in range(n_out):
        a, b = edges[u], edges[u + 1]
        i0, i1 = int(np.floor(a)), int(np.ceil(b))
        for i in range(max(i0, 0), min(i1, n_src)):
            A[u, i] = max(0.0, min(b, i + 1) - max(a, i))
        A[u] /= A[u].sum()
    return A


def bilinear_matrix(axis, n_src, n_out):
    """Display pixel x samples the squished frame at U(x) (bilinear)."""
    pos = axis.forward((np.arange(n_src) + 0.5) / n_src) * n_out - 0.5
    pos = np.clip(pos, 0, n_out - 1)
    i0 = np.floor(pos).astype(int)
    i1 = np.minimum(i0 + 1, n_out - 1)
    w = pos - i0
    B = np.zeros((n_src, n_out))
    B[np.arange(n_src), i0] += 1 - w
    B[np.arange(n_src), i1] += w
    return B


def regions():
    y, x = np.mgrid[0:H, 0:W]
    dx, dy = np.abs((x + 0.5) / W - 0.5), np.abs((y + 0.5) / H - 0.5)
    m = np.maximum(dx, dy)
    return {"fovea": m < 0.15, "middle": (m >= 0.15) & (m < 0.3), "outer": m >= 0.3}


def psnr(ref, test, mask):
    mse = np.mean([np.mean((fb.luma(a)[mask] - fb.luma(b)[mask]) ** 2) for a, b in zip(ref, test)])
    return 10 * np.log10(1 / max(mse, 1e-12))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    configs = [("baseline (no squish)", None)]
    for r, p in ((0.75, 0.5), (0.75, 1.0), (0.75, 1.5), (0.65, 0.5), (0.65, 0.75)):
        configs.append((f"X+Y r={r} p={p}", (r, p)))
    reg = regions()
    for clip, kbps in (("fps_pan_clean", 250), ("fps_pan_grain", 300)):
        src = list(fb.h.clip_frames(clip, n))
        print(f"\n== CBR {kbps} kbps, {clip}")
        print(f"   {'variant':<26}{'squished size':>15}{'fovea':>8}{'middle':>8}{'outer':>8}  (luma PSNR dB)")
        for name, cfg in configs:
            if cfg is None:
                enc, dec_fn, size = src, (lambda f: f), (W, H)
            else:
                r, p = cfg
                ax, ay = Axis(r=r, c=0.15, p=p), Axis(r=r, c=0.15, p=p)
                mw, mh = int(round(W * r / 2)) * 2, int(round(H * r / 2)) * 2
                ax.r, ay.r = mw / W, mh / H  # exact even sizes
                Ax, Ay = area_matrix(ax, W, mw), area_matrix(ay, H, mh)
                Bx, By = bilinear_matrix(ax, W, mw), bilinear_matrix(ay, H, mh)
                enc = [warp2(Ay, f, Ax) for f in src]
                dec_fn = lambda f, Bx=Bx, By=By: warp2(By, f, Bx)  # noqa: E731
                size = (mw, mh)
            path = os.path.join(fb.TMP, "p.mp4")
            fb.encode([np.ascontiguousarray(e) for e in enc], 60, 0, path, cbr_kbps=kbps)
            dec = [dec_fn(f) for f in fb.decode(path, size[0], size[1])]
            vals = [psnr(src, dec, reg[k]) for k in ("fovea", "middle", "outer")]
            print(f"   {name:<26}{f'{size[0]}x{size[1]}':>15}" + "".join(f"{v:8.2f}" for v in vals), flush=True)


if __name__ == "__main__":
    main()
