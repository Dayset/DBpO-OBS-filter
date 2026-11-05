"""Does the "Pixel Snapper" cleanup lower encoder bitrate? Numpy model + x264 measurement.

Models each stage of the proposed method separately and combined, then encodes with x264
veryfast at CRF 18/23 like plugins/perceptual-color-reduction/tests/bitrate_harness.py
(re-used for clips, color math, encoding and CIEDE2000). Edge-preserving flatten (the shipped
default of Perceptual Color Reduction) is included as the reference.

Stages (from the proposal):
  1 quantize   : N-color palette in Lab/OKLab (k-means via the plugin's palette.c), or 5-bit RGB grid
  2 edges      : 3x3 Sobel on luma -> edge strength 0..1
  3 snap/merge : edge pixels sample from their grid-cell center; flat pixels take the 3x3 dominant
                 (median) color
  4 resample   : nearest-neighbor, optionally at 0.75x internal resolution
  + temporal dither offset (Bayer pattern shifted every frame)

Usage: python3 pixel_snapper_bench.py [--frames 60] [--clips a,b] [--only name|name]
"""

import argparse
import os
import subprocess
import sys
import tempfile

import numpy as np
from scipy.ndimage import median_filter, sobel

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.join(HERE, "..", "..", "plugins", "perceptual-color-reduction", "tests")
sys.path.insert(0, TESTS)
import bitrate_harness as bh  # noqa: E402


def edge_strength(rgb):
    y = rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    g = np.hypot(sobel(y, 0), sobel(y, 1)) / 4.0  # 0..~1 for hard edges
    return np.clip(g * 4.0, 0, 1)


def nn_resample(img, scale):
    """Nearest-neighbor down to `scale` then back up (no filtering anywhere)."""
    h, w, _ = img.shape
    hs, ws = int(round(h * scale)), int(round(w * scale))
    yi = np.minimum((np.arange(hs) / scale).astype(int), h - 1)
    xi = np.minimum((np.arange(ws) / scale).astype(int), w - 1)
    small = img[yi][:, xi]
    yo = np.minimum((np.arange(h) * scale).astype(int), hs - 1)
    xo = np.minimum((np.arange(w) * scale).astype(int), ws - 1)
    return small[yo][:, xo]


class Snapper:
    def __init__(self, lib=None, palette=0, rgb_bits=0, merge=False, snap=0, edge_thr=0.15,
                 scale=1.0, tdither=0.0, flatten=0.0):
        self.palette, self.rgb_bits, self.merge, self.snap = palette, rgb_bits, merge, snap
        self.edge_thr, self.scale, self.tdither, self.frame = edge_thr, scale, tdither, 0
        self.pal = bh.Filter(lib, "kmeans", colors=palette) if palette else None
        self.flat = bh.Filter(lib, "off", flatten=flatten) if flatten else None

    def __call__(self, rgb):
        h, w, _ = rgb.shape
        out = rgb
        if self.flat is not None:
            out = self.flat(out)
        if self.tdither > 0:  # Bayer4 pattern shifted each frame, applied before quantization
            m = np.roll(bh.BAYER4, (self.frame % 4, (self.frame // 4) % 4), (0, 1))
            out = out + (np.tile(m, (h // 4 + 1, w // 4 + 1))[:h, :w, None] * self.tdither / 8)
        if self.rgb_bits:
            n = (1 << self.rgb_bits) - 1
            out = np.round(np.clip(out, 0, 1) * n) / n
        if self.pal is not None:
            out = self.pal(np.clip(out, 0, 1))
        if self.merge or self.snap:
            edges = edge_strength(out)
            flat = edges < self.edge_thr
            res = out
            if self.merge:  # flat areas -> 3x3 dominant (median) color
                med = np.stack([median_filter(out[..., c], size=3) for c in range(3)], -1)
                res = np.where(flat[..., None], med, res)
            if self.snap:  # edge pixels -> sample at grid-cell center
                g = self.snap
                yi = np.minimum((np.arange(h) // g) * g + g // 2, h - 1)
                xi = np.minimum((np.arange(w) // g) * g + g // 2, w - 1)
                cell = out[yi][:, xi]
                res = np.where(flat[..., None], res, cell)
            out = res
        if self.scale != 1.0:
            out = nn_resample(out, self.scale)
        self.frame += 1
        return np.clip(out, 0, 1).astype(np.float32)


def variants(lib):
    def S(**kw):
        return lambda: Snapper(lib, **kw)
    return [
        ("original", S()),
        ("flatten 0.02 (shipped)", S(flatten=0.02)),
        ("S1 palette 128", S(palette=128)),
        ("S1 palette 64", S(palette=64)),
        ("S1 RGB 5-bit grid", S(rgb_bits=5)),
        ("S3 flat 3x3 merge", S(merge=True)),
        ("S3 grid snap 2px edges", S(snap=2)),
        ("S3 merge + snap 2px", S(merge=True, snap=2)),
        ("S4 NN 0.75x", S(scale=0.75)),
        ("S4 NN 0.5x", S(scale=0.5)),
        ("temporal dither only", S(tdither=0.4)),
        ("full: pal128+merge+snap+tdither", S(palette=128, merge=True, snap=2, tdither=0.2)),
        ("full strong: pal64+merge+snap+0.75x", S(palette=64, merge=True, snap=2, tdither=0.4, scale=0.75)),
        ("flatten + merge", S(flatten=0.02, merge=True)),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--clips", default="fps_pan_clean,fps_pan_grain,static_noisy,smooth_gradients")
    ap.add_argument("--only")
    ap.add_argument("--clip", help="real lossless recording instead of synthetic clips")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp()
    libp = os.path.join(tmp, "libpalette.so")
    subprocess.check_call(["gcc", "-O2", "-shared", "-fPIC", "-std=c17", "-I", f"{TESTS}/../src",
                           f"{TESTS}/../src/palette.c", "-lm", "-o", libp])
    lib = bh.load_lib(libp)
    vs = variants(lib)
    if args.only:
        keep = args.only.split("|")
        vs = [v for v in vs if v[0] in keep or v[0] == "original"]

    clips = ["your clip"] if args.clip else args.clips.split(",")
    for cname in clips:
        src = list(bh.read_clip(args.clip, args.frames) if args.clip else bh.clip_frames(cname, args.frames))
        print(f"\n== {cname} ({len(src)} frames) x264 veryfast")
        print(f"   {'variant':<38}{'CRF18 kbps':>11}{'vs orig':>9}{'CRF23 kbps':>11}{'vs orig':>9}"
              f"{'mean dE':>9}{'p95 dE':>8}")
        base = {}
        for vname, make in vs:
            flt = make()
            out = [flt(f) for f in src]
            cols = ""
            for crf in (18, 23):
                r = bh.encode(out, os.path.join(tmp, "o.mp4"), crf=crf)
                base.setdefault(crf, r)
                cols += f"{r:11.0f}{(r / base[crf] - 1) * 100:+8.1f}%"
            mde, p95 = (0.0, 0.0) if vname == "original" else bh.de2000(src, out)
            print(f"   {vname:<38}{cols}{mde:9.2f}{p95:8.2f}", flush=True)


if __name__ == "__main__":
    main()
