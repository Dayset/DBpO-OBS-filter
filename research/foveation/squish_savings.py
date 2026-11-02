"""How much bitrate does the squish save? (feeds the plugin's estimate)

For each squeeze r (per side), a 10% full-quality center (c = 0.05) and a
near-flat curve, the stream is encoded at a fixed CBR budget B. Then a normal
(unsquished) stream is encoded over a bitrate sweep to find the bitrate B'
that gives the same center quality. Saving = 1 - B / B': the bitrate you no
longer need for the same center. Luma PSNR of the 10% center box.

Usage: python3 research/foveation/squish_savings.py [frames] [kbps] [r,r,...]
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import foveation_bench as fb  # noqa: E402
import squish_profiles as sp  # noqa: E402
from squish_math import Axis  # noqa: E402

H, W = fb.H, fb.W
C = 0.05  # half-size: 10% center
R_LIST = (1.0, 0.75, 0.6, 0.5, 0.4, 0.3, 0.25, 0.2)


def center_mask(c=C):
    y, x = np.mgrid[0:H, 0:W]
    return (np.abs((x + 0.5) / W - 0.5) < c) & (np.abs((y + 0.5) / H - 0.5) < c)


def encode_dec(frames, kbps, w, h):
    path = os.path.join(fb.TMP, "s.mp4")
    real = fb.encode([np.ascontiguousarray(f) for f in frames], 60, 0, path, cbr_kbps=kbps)
    return fb.decode(path, w, h), real


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 250
    global R_LIST
    if len(sys.argv) > 3:
        R_LIST = tuple(float(v) for v in sys.argv[3].split(","))
    mask = center_mask()
    for clip in ("fps_pan_clean", "fps_pan_grain"):
        src = list(fb.h.clip_frames(clip, n))
        # Baseline curve: center PSNR vs bitrate.
        sweep = []
        for kbps in (budget * f for f in (0.6, 0.8, 1, 1.25, 1.6, 2, 2.5, 3.2, 4, 5, 6.5, 8)):
            dec, real = encode_dec(src, int(kbps), W, H)
            sweep.append((real, sp.psnr(src, dec, mask)))
        sweep.sort()
        rates, qs = np.array([s[0] for s in sweep]), np.maximum.accumulate([s[1] for s in sweep])
        print(f"\n== {clip}, budget {budget} kbps, 10% center. Baseline: "
              + ", ".join(f"{r:.0f}k={q:.1f}" for r, q in zip(rates, qs)), flush=True)
        print(f"   {'squeeze/side':>12}{'pixels':>8}{'streamed':>11}{'center dB':>11}{'same center needs':>19}{'saving':>8}")
        for r in R_LIST:
            if r == 1.0:
                dec, real = encode_dec(src, budget, W, H)
                out, size = dec, (W, H)
            else:
                ax, ay = Axis(r=r, c=C, p=0.1), Axis(r=r, c=C, p=0.1)
                mw, mh = int(round(W * r / 2)) * 2, int(round(H * r / 2)) * 2
                ax.r, ay.r = mw / W, mh / H
                if ax.dmin() <= 0 or ay.dmin() <= 0:
                    continue
                Ax, Ay = sp.area_matrix(ax, W, mw), sp.area_matrix(ay, H, mh)
                Bx, By = sp.bilinear_matrix(ax, W, mw), sp.bilinear_matrix(ay, H, mh)
                dec, real = encode_dec([sp.warp2(Ay, f, Ax) for f in src], budget, mw, mh)
                out, size = [sp.warp2(By, d, Bx) for d in dec], (mw, mh)
            q = sp.psnr(src, out, mask)
            if q >= qs[-1]:
                need, note = rates[-1], ">"
            else:
                need, note = float(np.interp(q, qs, rates)), ""
            print(f"   {r:12.2f}{100 * size[0] * size[1] / (W * H):7.0f}%{f'{size[0]}x{size[1]}':>11}{q:11.2f}"
                  f"{note + f'{need:.0f} kbps':>19}{100 * (1 - real / need):7.0f}%", flush=True)


if __name__ == "__main__":
    main()
