"""Bitrate vs. color-error harness for perceptual color reduction.

Runs synthetic game-like clips through a numpy model of the shader (OKLab
posterize / palette LUT, dither, temporal hysteresis) using the plugin's own C
palette code (ctypes), encodes each variant with x264 like a stream would, and
reports bitrate and CIEDE2000 against the unprocessed clip.

Synthetic content is indicative only: real footage should be measured with
--clip path/to/lossless.mkv.

Usage: python3 bitrate_harness.py [--quick] [--clip FILE] [--lib libpalette.so]
"""

import argparse
import ctypes
import os
import subprocess
import sys
import tempfile

import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates, zoom
from skimage.color import deltaE_ciede2000, rgb2lab

# ------------------------------------------------------------------ color


def srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
               [0.2119034982, 0.6806995451, 0.1073969566],
               [0.0883024619, 0.2817188376, 0.6299787005]], np.float32)
M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
               [1.9779984951, -2.4285922050, 0.4505937099],
               [0.0259040371, 0.7827717662, -0.8086757660]], np.float32)
M2i = np.linalg.inv(M2).astype(np.float32)
M1i = np.linalg.inv(M1).astype(np.float32)


def to_oklab(rgb):
    return np.cbrt(srgb_to_linear(rgb) @ M1.T) @ M2.T


def from_oklab(lab):
    lms = (lab @ M2i.T) ** 3
    return linear_to_srgb(lms @ M1i.T)


BAYER4 = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) + 0.5) / 16 - 0.5
BAYER8 = None


def bayer8():
    global BAYER8
    if BAYER8 is None:
        b = np.array([[0, 2], [3, 1]])
        for _ in range(2):
            b = np.block([[4 * b, 4 * b + 2], [4 * b + 3, 4 * b + 1]])
        BAYER8 = (b + 0.5) / 64 - 0.5
    return BAYER8


def dither_map(h, w, kind):
    if kind == "bayer4":
        return np.tile(BAYER4, (h // 4 + 1, w // 4 + 1))[:h, :w]
    if kind == "bayer8":
        return np.tile(bayer8(), (h // 8 + 1, w // 8 + 1))[:h, :w]
    return np.zeros((h, w))


# ------------------------------------------------------------------ C palette


class Palette(ctypes.Structure):
    _fields_ = [("n", ctypes.c_int), ("c", (ctypes.c_float * 3) * 256)]


def load_lib(path):
    lib = ctypes.CDLL(path)
    fp = ctypes.POINTER(ctypes.c_float)
    lib.palette_octree.argtypes = [fp, ctypes.c_size_t, ctypes.c_int, ctypes.POINTER(Palette)]
    lib.palette_kmeans.argtypes = [fp, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_float,
                                   ctypes.POINTER(Palette), ctypes.POINTER(Palette)]
    lib.palette_stabilize.argtypes = [ctypes.POINTER(Palette), ctypes.POINTER(Palette), ctypes.c_float, ctypes.c_float]
    return lib


def pal_array(p):
    return np.array([[p.c[i][j] for j in range(3)] for i in range(p.n)], np.float32)


# ------------------------------------------------------------------ filter model


class Filter:
    """Mirror of the shader: optional flatten, quantize, dither, hysteresis."""

    def __init__(self, lib, mode, levels_l=32, levels_c=12, colors=128, dither="none", dither_strength=1.0,
                 stability=0.0, flatten=0.0, luma_weight=1.0, update_every=3, smoothing=0.8,
                 temporal=0.0, chroma_blur=0.0):
        self.lib, self.mode = lib, mode
        self.step_l, self.step_c = 1.0 / levels_l, 0.64 / levels_c
        self.colors, self.dither, self.dstrength = colors, dither, dither_strength
        self.stability, self.flatten, self.lw = stability, flatten, luma_weight
        self.update_every, self.smoothing = update_every, smoothing
        self.temporal, self.chroma_blur = temporal, chroma_blur
        self.prev_out = None
        self.pal = Palette()
        self.lut = None
        self.frame = 0

    def _build_lut(self, pal):
        # 64^3 RGB grid -> nearest palette entry (OKLab, luma weighted), as the GPU LUT pass does
        g = (np.arange(64) + 0.5) / 64
        grid = np.stack(np.meshgrid(g, g, g, indexing="ij"), -1).reshape(-1, 3).astype(np.float32)
        lab = to_oklab(grid)
        w = np.array([self.lw, 1, 1], np.float32)
        best = np.empty(len(lab), np.int32)
        for s in range(0, len(lab), 32768):
            d = (((lab[s:s + 32768, None, :] - pal[None, :, :]) * w) ** 2).sum(-1)
            best[s:s + 32768] = d.argmin(1)
        self.lut = pal[best].reshape(64, 64, 64, 3)

    def _palette_update(self, lab):
        sub = np.ascontiguousarray(lab[::6, ::6].reshape(-1, 3), np.float32)  # ~160x90 like the plugin
        ptr = sub.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        if self.mode == "octree":
            fresh = Palette()
            self.lib.palette_octree(ptr, len(sub), self.colors, ctypes.byref(fresh))
            if self.pal.n:
                self.lib.palette_stabilize(ctypes.byref(self.pal), ctypes.byref(fresh), self.smoothing, self.lw)
            self.pal = fresh
        else:  # k-means, warm-started: one Lloyd iteration per update
            iters = 1 if self.pal.n == self.colors else 6
            self.lib.palette_kmeans(ptr, len(sub), self.colors, iters, self.lw, ctypes.byref(self.pal),
                                    ctypes.byref(self.pal))
        self._build_lut(pal_array(self.pal))

    def __call__(self, rgb):
        h, w, _ = rgb.shape
        lab = to_oklab(rgb)
        if self.flatten > 0:
            # 5x5 edge-preserving smoothing in OKLab (range sigma = flatten)
            acc = np.zeros_like(lab)
            wsum = np.zeros((h, w, 1), np.float32)
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    sh = np.roll(lab, (dy, dx), (0, 1))
                    d2 = ((sh - lab) ** 2).sum(-1, keepdims=True)
                    wt = np.exp(-d2 / (2 * self.flatten ** 2)) * np.exp(-(dx * dx + dy * dy) / 4.5)
                    acc += sh * wt
                    wsum += wt
            lab = acc / wsum
        if self.chroma_blur > 0:
            for c in (1, 2):
                lab[..., c] = gaussian_filter(lab[..., c], self.chroma_blur)
        d = dither_map(h, w, self.dither)[..., None] * self.dstrength
        if self.mode == "off":
            q = lab
        elif self.mode == "posterize":
            steps = np.array([self.step_l, self.step_c, self.step_c], np.float32)
            q = np.floor(lab / steps + 0.5 + d) * steps
        else:
            if self.frame % self.update_every == 0 or self.lut is None:
                self._palette_update(lab)
            rgb_in = np.clip(from_oklab(lab) + d / 32.0, 0, 1)
            idx = np.minimum((rgb_in * 64).astype(np.int32), 63)
            q = self.lut[idx[..., 0], idx[..., 1], idx[..., 2]]
        if self.temporal > 0 and self.prev_out is not None:
            # Motion-adaptive recursive filter: small differences (noise) are
            # averaged into the previous output, large ones (motion) pass.
            dist = np.sqrt((((q - self.prev_out) * np.array([self.lw, 1, 1])) ** 2).sum(-1, keepdims=True))
            k = np.clip(dist / self.temporal, 0.25, 1.0)
            q = self.prev_out + (q - self.prev_out) * k
        if self.stability > 0 and self.prev_out is not None:
            # Keep last frame's output where the source hasn't moved away from it.
            dist = np.sqrt((((lab - self.prev_out) * np.array([self.lw, 1, 1])) ** 2).sum(-1))
            q = np.where((dist < self.stability)[..., None], self.prev_out, q)
        self.prev_out = q
        self.frame += 1
        return np.clip(from_oklab(q), 0, 1)


# ------------------------------------------------------------------ clips


def fractal(shape, seed, octaves=6, base=8):
    rng = np.random.default_rng(seed)
    out = np.zeros(shape, np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        cells = (max(2, int(shape[0] * (2 ** o) / (base * 64))), max(2, int(shape[1] * (2 ** o) / (base * 64))))
        g = rng.random(cells).astype(np.float32)
        out += amp * zoom(g, (shape[0] / cells[0], shape[1] / cells[1]), order=3)[:shape[0], :shape[1]]
        tot += amp
        amp *= 0.55
    return out / tot


def make_world(seed=1, h=700, w=3000):
    n1, n2, n3 = fractal((h, w), seed), fractal((h, w), seed + 1, base=2), fractal((h, w), seed + 2, octaves=8, base=1)
    grass = np.stack([0.18 + 0.1 * n2, 0.38 + 0.22 * n2, 0.12 + 0.08 * n3], -1)
    rock = np.stack([0.42 + 0.25 * n3, 0.40 + 0.24 * n3, 0.38 + 0.22 * n3], -1)
    t = np.clip((n1 - 0.45) * 6, 0, 1)[..., None]
    world = grass * (1 - t) + rock * t
    detail = fractal((h, w), seed + 3, octaves=3, base=0.25)  # foliage / gravel micro-texture
    world = world * (0.8 + 0.4 * detail[..., None])
    shade = 0.75 + 0.35 * gaussian_filter(n1, 6)[..., None]
    return np.clip(world * shade, 0, 1).astype(np.float32)


def sky(h, w):
    y = np.linspace(0, 1, h)[:, None, None]
    top, horizon = np.array([0.22, 0.42, 0.78]), np.array([0.72, 0.82, 0.92])
    return np.broadcast_to(top * (1 - y) + horizon * y, (h, w, 3)).astype(np.float32)


def hud(frame):
    h, w, _ = frame.shape
    f = frame.copy()
    f[h - 60:h - 20, 30:330] = f[h - 60:h - 20, 30:330] * 0.35 + 0.05  # health panel
    f[h - 52:h - 28, 40:250] = [0.85, 0.15, 0.12]
    for i in range(12):  # "text"
        f[30:42, w - 300 + i * 22:w - 288 + i * 22] = 0.95
    f[20:200, 20:200] = f[20:200, 20:200] * 0.5 + np.array([0.05, 0.1, 0.05])  # minimap frame
    return f


def clip_frames(kind, n, h=540, w=960, seed=5):
    rng = np.random.default_rng(seed)
    world = make_world()
    sk = sky(h // 3, w)
    if kind == "smooth_gradients":
        world = gaussian_filter(world, (12, 12, 0))
    for i in range(n):
        if kind == "static_noisy":
            x0 = 600
        elif kind == "smooth_gradients":
            x0 = 600 + i
        else:
            x0 = 600 + 6 * i
        ground = world[100:100 + h - h // 3, x0:x0 + w]
        f = np.concatenate([sk, ground], 0).copy()
        # moving sprites (players / vehicles)
        for k, (sy, spd, col) in enumerate(((300, 9, (0.75, 0.2, 0.15)), (380, -7, (0.2, 0.3, 0.75)))):
            sx = (200 + spd * i * (1 if kind != "static_noisy" else 0.5)) % (w - 80)
            f[sy:sy + 40, int(sx):int(sx) + 70] = col
        f = hud(f)
        if kind in ("fps_pan_grain", "static_noisy"):
            f = f + rng.normal(0, 4.0 / 255, f.shape)  # film grain / dithering in the game
        yield np.clip(f, 0, 1).astype(np.float32)


def read_clip(path, n, w=960, h=540):
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-frames:v", str(n), "-vf", f"scale={w}:{h}", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    while True:
        buf = p.stdout.read(w * h * 3)
        if len(buf) < w * h * 3:
            break
        yield np.frombuffer(buf, np.uint8).reshape(h, w, 3).astype(np.float32) / 255


# ------------------------------------------------------------------ measure


def encode(frames, out, crf=23, preset="veryfast"):  # noqa: D401
    h, w, _ = frames[0].shape
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", "60",
           "-i", "-", "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-g", "120",
           out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write((np.clip(f, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())
    p.stdin.close()
    p.wait()
    return os.path.getsize(out) * 8 / (len(frames) / 60) / 1000  # kbps


def de2000(a, b, every=6):
    vals = []
    for i in range(0, len(a), every):
        x = rgb2lab((np.clip(a[i], 0, 1) * 255 + 0.5).astype(np.uint8)[::2, ::2])
        y = rgb2lab((np.clip(b[i], 0, 1) * 255 + 0.5).astype(np.uint8)[::2, ::2])
        vals.append(deltaE_ciede2000(x, y).ravel())
    v = np.concatenate(vals)
    return v.mean(), np.percentile(v, 95)


CRFS = (18, 23)

VARIANTS = [
    ("original", dict(mode="off")),
    ("freeze only (stability 0.010)", dict(mode="off", stability=0.010)),
    ("freeze only (stability 0.020)", dict(mode="off", stability=0.020)),
    ("posterize L48/C16", dict(mode="posterize", levels_l=48, levels_c=16)),
    ("posterize L32/C12", dict(mode="posterize", levels_l=32, levels_c=12)),
    ("posterize L32/C12 + bayer4", dict(mode="posterize", levels_l=32, levels_c=12, dither="bayer4")),
    ("posterize L32/C12 + stability", dict(mode="posterize", levels_l=32, levels_c=12, stability=0.030)),
    ("flatten + posterize L32/C12 + stab.", dict(mode="posterize", levels_l=32, levels_c=12, stability=0.030,
                                                  flatten=0.02)),
    ("posterize L96/C32 + stability", dict(mode="posterize", levels_l=96, levels_c=32, stability=0.012)),
    ("flatten only (edge-preserving 0.02)", dict(mode="off", flatten=0.02)),
    ("temporal denoise (soft, 0.03)", dict(mode="off", temporal=0.03)),
    ("chroma smooth (sigma 1.5)", dict(mode="off", chroma_blur=1.5)),
    ("flatten + temporal + chroma", dict(mode="off", flatten=0.02, temporal=0.03, chroma_blur=1.5)),
    ("octree 128", dict(mode="octree", colors=128)),
    ("octree 128 + stability", dict(mode="octree", colors=128, stability=0.030)),
    ("k-means 128", dict(mode="kmeans", colors=128)),
    ("k-means 128 + stability", dict(mode="kmeans", colors=128, stability=0.030)),
    ("k-means 128 + bayer4 + stability", dict(mode="kmeans", colors=128, dither="bayer4", stability=0.030)),
    ("k-means 256 + stability", dict(mode="kmeans", colors=256, stability=0.015)),
]


QUICK = ("original", "posterize L32/C12", "k-means 128 + stability", "flatten + temporal + chroma")

SWEEP = [
    ("original", dict(mode="off")),
    ("flatten 0.010", dict(mode="off", flatten=0.010)),
    ("flatten 0.015", dict(mode="off", flatten=0.015)),
    ("flatten 0.020", dict(mode="off", flatten=0.020)),
    ("flatten 0.030", dict(mode="off", flatten=0.030)),
    ("flatten 0.040", dict(mode="off", flatten=0.040)),
    ("flatten 0.020 + temporal 0.02", dict(mode="off", flatten=0.020, temporal=0.02)),
    ("flatten 0.020 + hold 0.008", dict(mode="off", flatten=0.020, stability=0.008)),
]


def check(lib):
    """Fast CI assertion of the headline findings on the grainy clip."""
    src = list(clip_frames("fps_pan_grain", 24))
    tmp = tempfile.mkdtemp()
    rate = {}
    for name, kw in (("original", dict(mode="off")), ("flatten", dict(mode="off", flatten=0.02)),
                     ("posterize", dict(mode="posterize", levels_l=32, levels_c=12))):
        f = Filter(lib, **kw)
        rate[name] = encode([f(x) for x in src], os.path.join(tmp, "c.mp4"), crf=18)
        print(f"check: {name:<10} {rate[name]:7.0f} kbps (CRF 18)")
    ok = rate["flatten"] < rate["original"] * 0.9 and rate["posterize"] > rate["original"]
    print("check:", "PASS (flatten saves >10%, posterize costs bits)" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="fewer frames/variants (CI)")
    ap.add_argument("--clip", help="measure a real recording instead of synthetic clips")
    ap.add_argument("--frames", type=int, default=90)
    ap.add_argument("--lib", default=None)
    ap.add_argument("--sweep", action="store_true", help="flatten strength sweep")
    ap.add_argument("--clips", help="comma-separated synthetic clip names")
    ap.add_argument("--only", help="comma-separated variant names")
    ap.add_argument("--check", action="store_true", help="fast pass/fail check of headline findings (CI)")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    tmp = tempfile.mkdtemp()
    lib_path = args.lib or os.path.join(tmp, "libpalette.so")
    if not args.lib:
        subprocess.check_call(["gcc", "-O2", "-shared", "-fPIC", "-std=c17", "-I", f"{here}/../src",
                               f"{here}/../src/palette.c", "-lm", "-o", lib_path])
    lib = load_lib(lib_path)
    if args.check:
        sys.exit(0 if check(lib) else 1)

    n = 30 if args.quick else args.frames
    clips = [("your clip", None)] if args.clip else [("fps_pan_clean", None), ("fps_pan_grain", None),
                                                       ("static_noisy", None), ("smooth_gradients", None)]
    if args.clips:
        clips = [(c, None) for c in args.clips.split(",")]
    variants = SWEEP if args.sweep else (VARIANTS if not args.quick else [v for v in VARIANTS if v[0] in QUICK])
    if args.only:
        keep = args.only.split("|")
        variants = [v for v in VARIANTS + SWEEP if v[0] in keep]
        variants = [v for i, v in enumerate(variants) if v[0] not in [w[0] for w in variants[:i]]]
    results = {}
    for cname, _ in clips:
        src = list(read_clip(args.clip, n) if args.clip else clip_frames(cname, n))
        print(f"\n== {cname} ({len(src)} frames, {src[0].shape[1]}x{src[0].shape[0]}) — x264 veryfast")
        hdr = "".join(f"{'CRF' + str(c) + ' kbps':>12}{'vs orig':>9}" for c in CRFS)
        print(f"   {'variant':<38}{hdr}{'mean dE2000':>13}{'p95 dE':>8}")
        base = {}
        for vname, kw in variants:
            flt = Filter(lib, **kw)
            out = [flt(f) for f in src]
            cols = ""
            rates = {}
            for crf in CRFS:
                rates[crf] = encode(out, os.path.join(tmp, "o.mp4"), crf=crf)
                base.setdefault(crf, rates[crf])
                cols += f"{rates[crf]:12.0f}{(rates[crf] / base[crf] - 1) * 100:+8.1f}%"
            mde, p95 = (0.0, 0.0) if vname == "original" else de2000(src, out)
            results[(cname, vname)] = (rates, mde, p95)
            print(f"   {vname:<38}{cols}{mde:13.2f}{p95:8.2f}", flush=True)
    return results


if __name__ == "__main__":
    main()
