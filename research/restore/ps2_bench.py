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
sys.path.insert(0, HERE)
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


RESULTS = os.path.join(HERE, "results_ps2.json")
TEST = range(300, N, 5)  # every 5th test frame: 60 frames, enough for a stable mean at this cost
SCALE = {"540": 2, "360": 3}


def unsharp(f, amt):
    return np.clip(f + amt * (f - cv2.GaussianBlur(f, (0, 0), 1.5)), 0, 1)


def score(pred255, gt255):
    """Luma PSNR / SSIM over the whole frame, the centre box and the periphery, plus edge sharpness."""
    import starvation_bench as sb
    import restore_bench as rb
    a, b = sb.luma(gt255), sb.luma(pred255)
    per = np.ones(a.shape, bool)
    per[sb.CENTER] = False
    sm = sb.ssim_map(b, a)
    mse_p = float(((a - b)[per] ** 2).mean())
    gg = rb.sobel_mag(a[None] / 255)[0]
    gp = rb.sobel_mag(b[None] / 255)[0]
    edge = gg > np.percentile(gg, 90)
    return dict(psnr=sb.psnr(a, b), psnr_c=sb.psnr(a[sb.CENTER], b[sb.CENTER]),
                psnr_p=99.0 if mse_p < 1e-6 else 10 * np.log10(255 ** 2 / mse_p),
                ssim=float(sm.mean()), ssim_c=float(sm[sb.CENTER].mean()), ssim_p=float(sm[per].mean()),
                edge_sharpness=float(gp[edge].mean() / gg[edge].mean()))


def eval_cell(kbps, level):
    import restore_math as rm
    import starvation_bench as sb  # noqa: F401 (imported here so score() can use it)
    key = f"{kbps}/{level}"
    res = json.load(open(RESULTS)) if os.path.exists(RESULTS) else {}
    if key + "/" + ("stream" if level == "1080" else "restore+unsharp1.0") in res:
        print(key, "already done", flush=True)
        return
    mkv = cell_path(kbps, level)
    w, h = SIZES[level]
    ref = ref_frames()
    weights = None if level == "1080" else rm.load(os.path.join(HERE, "out", f"restorer_L{level}.rstr"))
    rows = {}
    for i, lo in enumerate(rf.frames(mkv, w, h)):
        if i not in TEST:
            continue
        gt = np.asarray(ref[i]).astype(np.float32)
        if level == "1080":
            arms = {"stream": lo.astype(np.float32)}
        else:
            lo_f = lo.astype(np.float32) / 255
            rest = rm.restore(lo_f, weights, SCALE[level]) * 255
            arms = {"bicubic": cv2.resize(lo, (W, H), interpolation=cv2.INTER_CUBIC).astype(np.float32),
                    "restore": np.clip(rest, 0, 255),
                    "restore+unsharp1.0": unsharp(rest / 255, 1.0) * 255}
        for arm, pred in arms.items():
            rows.setdefault(arm, []).append(score(pred, gt))
    out = json.load(open(RESULTS)) if os.path.exists(RESULTS) else {}
    for arm, ms in rows.items():
        out[f"{key}/{arm}"] = {k: round(float(np.mean([m[k] for m in ms])), 4) for k in ms[0]}
        out[f"{key}/{arm}"]["frames"] = len(ms)
        print(f"{key}/{arm}", out[f"{key}/{arm}"], flush=True)
    json.dump(out, open(RESULTS, "w"), indent=2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("encode")
    e.add_argument("--kbps", type=int, required=True, choices=[3000, 6000])
    e.add_argument("--level", required=True, choices=list(SIZES))
    v = sub.add_parser("eval")
    v.add_argument("--kbps", type=int, required=True, choices=[3000, 6000])
    v.add_argument("--level", required=True, choices=list(SIZES))
    a = ap.parse_args()
    if a.cmd == "encode":
        encode(a.kbps, a.level)
    elif a.cmd == "eval":
        eval_cell(a.kbps, a.level)
