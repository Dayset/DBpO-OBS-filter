"""Measure the 'squish shader' branch of ideas with x264.

Usage: python3 research/foveation/foveation_bench.py [crf|cbr] [frames]
  crf  fixed quality: bitrate saved by each variant (+ 120 fps periphery rings)
  cbr  fixed bitrate (like a stream): where the bits go (center/edge PSNR)


A) Spatial (60 fps): baseline vs
   - foveated pre-blur only (viewer needs nothing)
   - squish: sides warped to 75% width, pre-blurred, encoded small, unwarped after decode
   - control: uniform downscale to the same 75% width, upscaled after decode
B) Temporal (120 fps stream): periphery refreshed at 60 / 40 fps with a feathered
   mask (freeze-filter rings), vs full 120, vs a plain 60 fps stream.

Quality: PSNR (luma) of the decoded, unwarped result vs the original, split into
center (|x - 0.5| < 0.15) and periphery.
"""
import os
import subprocess
import sys
import tempfile

import numpy as np
from scipy.ndimage import gaussian_filter1d

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "plugins", "perceptual-color-reduction", "tests"))
import bitrate_harness as h  # noqa: E402

H, W = 540, 960
TMP = tempfile.mkdtemp()
CENTER = 0.15  # half-width of the untouched center, fraction of frame width


def encode(frames, fps, crf, out, cbr_kbps=None):
    hh, ww, _ = frames[0].shape
    rc = (["-crf", str(crf)] if cbr_kbps is None else
          ["-b:v", f"{cbr_kbps}k", "-maxrate", f"{cbr_kbps}k", "-bufsize", f"{cbr_kbps}k",
           "-x264-params", "nal-hrd=cbr"])
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ww}x{hh}", "-r", str(fps),
           "-i", "-"] + ["-c:v", "libx264", "-preset", "veryfast"] + rc + ["-pix_fmt", "yuv420p",
           "-g", str(2 * fps), out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write((np.clip(f, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())
    p.stdin.close()
    p.wait()
    return os.path.getsize(out) * 8 / (len(frames) / fps) / 1000


def decode(path, ww, hh):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    return list(np.frombuffer(raw, np.uint8).reshape(-1, hh, ww, 3).astype(np.float32) / 255)


def luma(f):
    return f[..., 0] * 0.2126 + f[..., 1] * 0.7152 + f[..., 2] * 0.0722


def psnr_regions(ref, test):
    x = (np.arange(W) + 0.5) / W
    center = np.abs(x - 0.5) < CENTER
    out = []
    for mask in (center, ~center):
        mse = np.mean([np.mean((luma(a)[:, mask] - luma(b)[:, mask]) ** 2) for a, b in zip(ref, test)])
        out.append(10 * np.log10(1 / max(mse, 1e-12)))
    return out


# ------------------------------------------------------------------ warp

def density(x, d_min):
    """Output pixels per source pixel: 1 in the center, falling smoothly to d_min at the edges."""
    t = np.clip((np.abs(x - 0.5) - CENTER) / (0.5 - CENTER), 0, 1)
    s = t * t * (3 - 2 * t)
    return 1 - (1 - d_min) * s


def warp_tables(out_w):
    # find d_min so the warped width is out_w
    xs = (np.arange(W) + 0.5) / W
    lo, hi = 0.0, 1.0
    for _ in range(50):
        mid = (lo + hi) / 2
        if density(xs, mid).sum() > out_w:
            hi = mid
        else:
            lo = mid
    d = density(xs, (lo + hi) / 2)
    u_of_x = np.concatenate([[0], np.cumsum(d)])  # source column edge -> output coordinate
    src_edges = np.arange(W + 1, dtype=np.float64)
    out_centers = np.arange(out_w) + 0.5
    x_of_u = np.interp(out_centers, u_of_x, src_edges)  # output column center -> source position
    u_centers = np.interp(np.arange(W) + 0.5, src_edges, u_of_x)  # source column center -> output position
    return d, x_of_u, u_centers


def resample_cols(img, pos):
    """Linear interpolation of columns at fractional positions (pixel-center convention)."""
    p = np.clip(pos - 0.5, 0, img.shape[1] - 1)
    i0 = np.floor(p).astype(int)
    i1 = np.minimum(i0 + 1, img.shape[1] - 1)
    w = (p - i0)[None, :, None]
    return img[:, i0] * (1 - w) + img[:, i1] * w


def preblur(img, d):
    """Horizontal low-pass whose strength follows the squeeze (sigma ~ 0.5/d - 0.5 px)."""
    sig = np.maximum(0.5 / d - 0.5, 0)
    levels = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]
    blurred = [img if s == 0 else gaussian_filter1d(img, s, axis=1) for s in levels]
    out = np.zeros_like(img)
    idx = np.interp(sig, levels, np.arange(len(levels)))
    lo = np.floor(idx).astype(int)
    hi = np.minimum(lo + 1, len(levels) - 1)
    t = (idx - lo)[None, :, None]
    for k in range(len(levels)):
        out += blurred[k] * ((lo == k)[None, :, None] * (1 - t) + (hi == k)[None, :, None] * t)
    return out


def dup_map(n, k_max=3):
    """In-place line doubling: periphery columns copy a neighbor (2x, then 3x near the edge)."""
    idx = np.arange(n)
    x = (idx + 0.5) / n
    t = np.clip((np.abs(x - 0.5) - CENTER) / (0.5 - CENTER), 0, 1)
    k = np.where(t <= 0, 1, np.where(t < 0.5, 2, k_max))
    out = idx.copy()
    i = 0
    while i < n:
        kk = int(k[i])
        out[i:i + kk] = min(i + kk // 2, n - 1)  # anchor = middle of the group
        i += kk
    return out


def variants_cbr(src, d, x_of_u, u_centers, d_y, y_of_v, v_centers):
    xs, ys = dup_map(W), dup_map(H)
    return {
        "baseline": (lambda f: f, None),
        "edge softening (blur, no player needed)": (lambda f: preblur(f, d), None),
        "line doubling X (in place, no player)": (lambda f: f[:, xs], None),
        "line doubling X+Y (in place, no player)": (lambda f: f[ys][:, xs], None),
        "squish X 75% + unwarp (needs player)": (lambda f: resample_cols(preblur(f, d), x_of_u), "x"),
        "squish X+Y 75%/75% + unwarp (needs player)": (
            lambda f: resample_cols(preblur(f, d), x_of_u).transpose(1, 0, 2), "xy"),
        "uniform 75% downscale + upscale": (
            lambda f: resample_cols(gaussian_filter1d(f, 0.6, axis=1), (np.arange(720) + 0.5) * W / 720), "u"),
    }


def main_cbr(n):
    d, x_of_u, u_centers = warp_tables(720)
    # vertical tables for X+Y: reuse the same profile on rows (540 -> 405)
    global W
    W_saved = W
    W = H
    d_y, y_of_v, v_centers = warp_tables(406)
    W = W_saved
    for clip, kbps in (("fps_pan_clean", 250), ("fps_pan_grain", 300)):
        src = list(h.clip_frames(clip, n))
        print(f"\n== CBR {kbps} kbps (fixed budget, like a stream), {clip}, 60 fps")
        print(f"   {'variant':<46}{'center PSNR':>12}{'edge PSNR':>10}{'actual kbps':>12}")
        for name, (fn, kind) in variants_cbr(src, d, x_of_u, u_centers, d_y, y_of_v, v_centers).items():
            enc = []
            for f in src:
                g = fn(f)
                if kind == "xy":  # rows squeezed too: g is transposed (W', H, 3) -> squeeze rows then back
                    g = resample_cols(preblur_rows(g, d_y), y_of_v).transpose(1, 0, 2)
                enc.append(np.ascontiguousarray(g))
            path = os.path.join(TMP, "cbr.mp4")
            rate = encode(enc, 60, 0, path, cbr_kbps=kbps)
            dec = decode(path, enc[0].shape[1], enc[0].shape[0])
            if kind == "x":
                dec = [resample_cols(f, u_centers) for f in dec]
            elif kind == "xy":
                dec = [resample_cols(resample_cols(f, u_centers).transpose(1, 0, 2), v_centers).transpose(1, 0, 2)
                       for f in dec]
            elif kind == "u":
                dec = [resample_cols(f, (np.arange(W) + 0.5) * 720 / W) for f in dec]
            pc, pe = psnr_regions(src, dec)
            print(f"   {name:<46}{pc:12.2f}{pe:10.2f}{rate:12.0f}", flush=True)


def preblur_rows(g_t, d_y):
    """g_t is transposed (cols, rows, 3): blur along rows axis=1 with the vertical profile."""
    return preblur(g_t, d_y)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "cbr"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    if mode == "cbr":
        return main_cbr(n)
    out_w = 720
    d, x_of_u, u_centers = warp_tables(out_w)
    print(f"squish: {W} -> {out_w} px wide; center {2 * CENTER:.0%} untouched; edge squeeze {1 / d.min():.1f}:1")

    for clip in ("fps_pan_clean", "fps_pan_grain"):
        src = list(h.clip_frames(clip, n))
        print(f"\n== A) spatial, {clip}, 60 fps, x264 veryfast")
        print(f"   {'variant':<44}{'CRF23 kbps':>11}{'vs base':>9}{'CRF18 kbps':>11}{'vs base':>9}"
              f"{'center PSNR':>12}{'edge PSNR':>10}")
        base = {}
        for name in ("baseline", "foveated pre-blur only", "squish 75% + unwarp", "uniform 75% + upscale"):
            rates = {}
            for crf in (23, 18):
                if name == "baseline":
                    enc = src
                elif name == "foveated pre-blur only":
                    enc = [preblur(f, d) for f in src]
                elif name == "squish 75% + unwarp":
                    enc = [resample_cols(preblur(f, d), x_of_u) for f in src]
                else:
                    enc = [resample_cols(gaussian_filter1d(f, 0.6, axis=1), (np.arange(out_w) + 0.5) * W / out_w)
                           for f in src]
                path = os.path.join(TMP, f"a{crf}.mp4")
                rates[crf] = encode(enc, 60, crf, path)
                base.setdefault(crf, rates[crf])
                if crf == 23:
                    dec = decode(path, enc[0].shape[1], H)
                    if name == "squish 75% + unwarp":
                        dec = [resample_cols(f, u_centers) for f in dec]
                    elif name == "uniform 75% + upscale":
                        dec = [resample_cols(f, (np.arange(W) + 0.5) * out_w / W) for f in dec]
                    pc, pe = psnr_regions(src, dec)
            print(f"   {name:<44}{rates[23]:11.0f}{(rates[23] / base[23] - 1) * 100:+8.1f}%"
                  f"{rates[18]:11.0f}{(rates[18] / base[18] - 1) * 100:+8.1f}%{pc:12.2f}{pe:10.2f}", flush=True)

    # B) temporal: 120 fps stream with slower pan per frame
    x = (np.arange(W) + 0.5) / W
    feather = 0.06
    mask = np.clip((np.abs(x - 0.5) - CENTER) / feather, 0, 1)[None, :, None]  # 0 center .. 1 periphery
    for clip in ("fps_pan_clean", "fps_pan_grain"):
        frames120 = list(clip_120(clip, 2 * n))
        print(f"\n== B) temporal, {clip}, 120 fps stream ({len(frames120)} frames), CRF 23")
        base = None
        for name, period in (("full 120 fps", 1), ("periphery 60 fps (feathered)", 2),
                             ("periphery 40 fps (feathered)", 3)):
            out = []
            held = None
            for i, f in enumerate(frames120):
                if i % period == 0:
                    held = f
                out.append(f * (1 - mask) + held * mask)
            r = encode(out, 120, 23, os.path.join(TMP, "b.mp4"))
            base = base or r
            print(f"   {name:<44}{r:11.0f}{(r / base - 1) * 100:+8.1f}%", flush=True)
        r60 = encode(frames120[::2], 60, 23, os.path.join(TMP, "b60.mp4"))
        print(f"   {'plain 60 fps stream (reference)':<44}{r60:11.0f}{(r60 / base - 1) * 100:+8.1f}%")


def clip_120(kind, n):
    """Same scene as the 60 fps clips, camera moving half as far per frame."""
    rng = np.random.default_rng(5)
    world = h.make_world()
    sk = h.sky(H // 3, W)
    for i in range(n):
        x0 = 600 + 3 * i
        ground = world[100:100 + H - H // 3, x0:x0 + W]
        f = np.concatenate([sk, ground], 0).copy()
        for sy, spd, col in ((300, 4.5, (0.75, 0.2, 0.15)), (380, -3.5, (0.2, 0.3, 0.75))):
            sx = (200 + spd * i) % (W - 80)
            f[sy:sy + 40, int(sx):int(sx) + 70] = col
        f = h.hud(f)
        if kind == "fps_pan_grain":
            f = f + rng.normal(0, 4.0 / 255, f.shape)
        yield np.clip(f, 0, 1).astype(np.float32)


if __name__ == "__main__":
    main()
