"""Real-capture pair builder (issue #24.1, epic #20).

    python3 research/restore/restore_data.py build --src WORK/ref.u8 --start 0 --count 60 --level 540 --kbps 6000

WORK/ref.u8 is the raw 1920x1080 RGB frame file that starvation_bench writes (uint8, frame-major).
One invocation handles at most 60 frames. Re-running the same range is a no-op.

For each frame range it writes, under out/data/:
  gt_{start:06d}_{count}.npy           sharp target, uint8 (count, 540, 960, 3): 1080p shrunk to the 960x540 picture
  lo_{start:06d}_{count}_L{level}_{kbps}.npy  decoded input, uint8 (count, 540/s, 960/s, 3): gt shrunk to the
                                       level, x264 veryfast CBR at kbps, decoded (same settings as the
                                       real_footage_bench encodes)
and appends the shard to out/data/manifest.json.
The sharp target is not blurred: real captures already contain the game's own motion blur.
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "foveation"))
import real_footage_bench as rf  # noqa: E402

OUT = os.path.join(HERE, "out", "data")
W, H = 1920, 1080  # capture size
PW, PH = 960, 540  # picture size (GT)
SCALE = {"540": 2, "360": 3}
MAX_COUNT = 60


def frame_count(src):
    return os.path.getsize(src) // (W * H * 3)


def tag(start, count, level, kbps):
    return f"{start:06d}_{count}_L{level}_{kbps}"


def load_manifest():
    p = os.path.join(OUT, "manifest.json")
    return json.load(open(p)) if os.path.exists(p) else {"shards": []}


def save_manifest(m):
    os.makedirs(OUT, exist_ok=True)
    tmp = os.path.join(OUT, "manifest.json.tmp")
    json.dump(m, open(tmp, "w"), indent=2)
    os.replace(tmp, os.path.join(OUT, "manifest.json"))


def build(src, start, count, level, kbps):
    if count > MAX_COUNT:
        raise SystemExit(f"--count must be <= {MAX_COUNT}")
    n = frame_count(src)
    if start + count > n:
        raise SystemExit(f"range {start}+{count} exceeds {n} frames in {src}")
    s = SCALE[level]
    lw, lh = PW // s, PH // s
    t = tag(start, count, level, kbps)
    m = load_manifest()
    gt_path = os.path.join(OUT, f"gt_{start:06d}_{count}.npy")
    lo_path = os.path.join(OUT, f"lo_{t}.npy")
    if any(x["tag"] == t for x in m["shards"]) and os.path.exists(lo_path) and os.path.exists(gt_path):
        print(f"{t}: already built", flush=True)
        return
    ref = np.memmap(src, np.uint8, "r", shape=(n, H, W, 3))
    gt = np.stack([cv2.resize(np.asarray(ref[i]), (PW, PH), interpolation=cv2.INTER_AREA)
                   for i in range(start, start + count)])
    lo_frames = (cv2.resize(f, (lw, lh), interpolation=cv2.INTER_AREA) for f in gt)
    os.makedirs(OUT, exist_ok=True)
    mkv = os.path.join(OUT, f"tmp_{t}.mkv")
    rf.encode(lo_frames, lw, lh, kbps, mkv)
    lo = np.stack(list(rf.frames(mkv, lw, lh)))
    assert len(lo) == count, (len(lo), count)
    bits = os.path.getsize(mkv) * 8 / count
    os.remove(mkv)
    np.save(gt_path, gt)
    np.save(lo_path, lo)
    m["shards"] = [x for x in m["shards"] if x["tag"] != t] + [dict(
        tag=t, start=start, count=count, level=level, kbps=kbps, bits_per_frame=round(bits),
        gt=os.path.basename(gt_path), lo=os.path.basename(lo_path))]
    save_manifest(m)
    print(f"{t}: {count} frames, {bits:.0f} bits/frame", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--src", required=True)
    b.add_argument("--start", type=int, required=True)
    b.add_argument("--count", type=int, default=MAX_COUNT)
    b.add_argument("--level", default="540", choices=list(SCALE))
    b.add_argument("--kbps", type=int, required=True)
    a = ap.parse_args()
    build(a.src, a.start, a.count, a.level, a.kbps)
