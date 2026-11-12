"""Low-res edge restorer benchmark (issue #22, epic #20).

    python3 research/restore/restore_bench.py make --clips 0-3 --levels 540,360

Clips come from the perceptual-color-reduction harness (bitrate_harness.clip_frames).
Test clips are ids 0-3, training clips ids 4-11 (they start 100 frames further along
the pan, so their crops differ from the test crops).

Degradation (prototype chain, claude/restore-prototype/exp1.py):
  sharp frame t -> directional blur (mean of frames t and t+1, a one-frame shutter)
  -> INTER_AREA downscale by s (540 = 2x, 360 = 3x of the 960x540 picture)
  -> x264 veryfast CRF 28 round trip.
Outputs in research/restore/out/ (gitignored), skipped when they already exist:
  out/gt/clip{id}.npy            sharp frames, uint8 (T, 540, 960, 3)
  out/pairs/clip{id}_L{level}.npy  decoded low-res input, uint8 (T, 540/s, 960/s, 3)
  out/pairs/clip{id}_L{level}.json bits per frame
"""
import argparse
import json
import os
import subprocess
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "plugins", "perceptual-color-reduction", "tests"))
import bitrate_harness as bh  # noqa: E402

OUT = os.path.join(HERE, "out")
W, H = 960, 540
T = 20  # frames per clip
KINDS = ("pan_clean", "pan_grain", "static_noisy", "smooth_gradients")
SCALE = {"540": 2, "360": 3}
TRAIN_SKIP = 100  # frames skipped so training crops differ from test crops


def clip_frames(cid):
    """Sharp frames for clip id: T+1 frames (one extra for the blur window)."""
    skip = TRAIN_SKIP if cid >= 4 else 0
    kind = KINDS[cid % len(KINDS)]
    frames = list(bh.clip_frames(kind, skip + T + 1, seed=5 + cid))[skip:]
    return np.stack([(f * 255 + 0.5).astype(np.uint8) for f in frames])


def blur(sharp):
    """One-frame shutter: mean of frame t and t+1, uint8, length T."""
    f = sharp.astype(np.float32)
    return ((f[:-1] + f[1:]) * 0.5 + 0.5).astype(np.uint8)


def x264_roundtrip(frames, crf=28):
    """Encode and decode uint8 RGB frames with x264 veryfast. Returns (decoded, bits)."""
    n, h, w, _ = frames.shape
    enc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", "60",
         "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
         "-f", "matroska", "-"], input=np.ascontiguousarray(frames).tobytes(), capture_output=True, check=True).stdout
    dec = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "-", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         input=enc, capture_output=True, check=True).stdout
    return np.frombuffer(dec, np.uint8).reshape(n, h, w, 3).copy(), len(enc) * 8


def ids(spec):
    a, _, b = spec.partition("-")
    return range(int(a), int(b or a) + 1)


def make(clips, levels):
    os.makedirs(f"{OUT}/gt", exist_ok=True)
    os.makedirs(f"{OUT}/pairs", exist_ok=True)
    for cid in clips:
        gt_path = f"{OUT}/gt/clip{cid}.npy"
        pairs = {lv: f"{OUT}/pairs/clip{cid}_L{lv}" for lv in levels}
        if os.path.exists(gt_path) and all(os.path.exists(p + ".npy") for p in pairs.values()):
            continue
        sharp = clip_frames(cid)
        if not os.path.exists(gt_path):
            np.save(gt_path, sharp[:T])
        bl = blur(sharp)
        for lv, pair in pairs.items():
            if os.path.exists(pair + ".npy"):
                continue
            s = SCALE[lv]
            src = np.stack([cv2.resize(b, (W // s, H // s), interpolation=cv2.INTER_AREA) for b in bl])
            dec, bits = x264_roundtrip(src)
            np.save(pair + ".npy", dec)
            json.dump({"bits_per_frame": bits / T, "crf": 28, "scale": s}, open(pair + ".json", "w"))
            print(f"clip {cid} level {lv}: {bits / T:.0f} bits/frame", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("--clips", required=True)
    m.add_argument("--levels", default="540,360")
    a = ap.parse_args()
    if a.cmd == "make":
        make(list(ids(a.clips)), a.levels.split(","))
