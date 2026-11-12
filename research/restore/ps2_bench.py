"""Matched CBR test on the public PlanetSide 2 timeline (issue #23, epic #20).

    python3 research/restore/ps2_bench.py encode --kbps 3000 --level 540   # one cell per call

Levels are stream heights, as in starvation_bench: 1080 = 1920x1080 (no change), 540 = 960x540 stream
(the restorer's x2 input), 360 = 640x360 stream (the restorer's x3 input). The restorer restores the stream
back to 1920x1080. Cells are x264 veryfast CBR, 60 fps, 2 s GOP, the same settings as real_footage_bench.
Source: WORK/ref.u8 (1920x1080 RGB, 600 frames) from starvation_bench.py build; split in configs/ps2_split.json.
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

WORK = os.path.join(HERE, "out", "ps2")
REF = os.path.join(WORK, "ref.u8")
W, H, N = 1920, 1080, 600
SIZES = {"1080": (1920, 1080), "540": (960, 540), "360": (640, 360)}


def ref_frames():
    return np.memmap(REF, np.uint8, "r", shape=(N, H, W, 3))


def cell_path(kbps, level):
    return os.path.join(WORK, "cells", f"{kbps}_{level}.mkv")


def stream_frames(level):
    w, h = SIZES[level]
    ref = ref_frames()
    for i in range(N):
        f = np.asarray(ref[i])
        yield f if level == "1080" else cv2.resize(f, (w, h), interpolation=cv2.INTER_AREA)


def encode(kbps, level):
    out = cell_path(kbps, level)
    meta = out + ".json"
    if os.path.exists(meta):
        print(f"{kbps}/{level}: already encoded", json.load(open(meta)), flush=True)
        return
    os.makedirs(os.path.dirname(out), exist_ok=True)
    w, h = SIZES[level]
    tmp = out + ".tmp.mkv"
    rf.encode(stream_frames(level), w, h, kbps, tmp)
    os.replace(tmp, out)
    kb = os.path.getsize(out) * 8 / (N / 60) / 1000
    info = {"kbps_target": kbps, "level": level, "size": [w, h], "kbps_measured": round(kb, 1), "frames": N}
    json.dump(info, open(meta, "w"), indent=2)
    print(f"{kbps}/{level}: {w}x{h}, target {kbps} kbit/s, measured {kb:.1f} kbit/s", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("encode")
    e.add_argument("--kbps", type=int, required=True, choices=[3000, 6000])
    e.add_argument("--level", required=True, choices=list(SIZES))
    a = ap.parse_args()
    if a.cmd == "encode":
        encode(a.kbps, a.level)
