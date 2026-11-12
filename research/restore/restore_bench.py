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
from skimage.metrics import structural_similarity

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


def cbr_roundtrip(frames, kbps, fps=60):
    """Encode and decode uint8 RGB frames as a fixed-bitrate stream (x264 veryfast, 2 s GOP, the same
    settings as foveation_bench.encode). Returns (decoded, kbit/s actually produced)."""
    n, h, w, _ = frames.shape
    enc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
         "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k",
         "-bufsize", f"{kbps}k", "-g", str(2 * fps), "-pix_fmt", "yuv420p", "-f", "matroska", "-"],
        input=np.ascontiguousarray(frames).tobytes(), capture_output=True, check=True).stdout
    dec = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "-", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         input=enc, capture_output=True, check=True).stdout
    return np.frombuffer(dec, np.uint8).reshape(n, h, w, 3).copy(), len(enc) * 8 / (n / fps) / 1000


def sobel_mag(x):
    """Sobel gradient magnitude of the channel mean, x: (N, H, W) float32."""
    gx = np.stack([cv2.Sobel(f, cv2.CV_32F, 1, 0, ksize=3) for f in x]) / 8
    gy = np.stack([cv2.Sobel(f, cv2.CV_32F, 0, 1, ksize=3) for f in x]) / 8
    return np.sqrt(gx * gx + gy * gy + 1e-8)


def metrics(pred, gt):
    """pred, gt: (T, H, W, 3) float32 in [0, 1]. PSNR and SSIM averaged over frames; edge sharpness is the
    gradient ratio on the top 10% GT edge pixels (1.0 = sharp original)."""
    p = np.clip(pred, 0, 1)
    psnr = np.mean([10 * np.log10(1 / max(np.mean((a - b) ** 2), 1e-10)) for a, b in zip(p, gt)])
    ssim = np.mean([structural_similarity(a, b, channel_axis=2, data_range=1.0) for a, b in zip(p, gt)])
    gp = sobel_mag(p.mean(-1))
    gg = sobel_mag(gt.mean(-1))
    edge = gg > np.percentile(gg, 90)
    sharp = gp[edge].mean() / gg[edge].mean()
    return {"psnr": round(float(psnr), 3), "ssim": round(float(ssim), 4), "edge_sharpness": round(float(sharp), 3)}


def load_pair(cid, lv):
    gt = np.load(f"{OUT}/gt/clip{cid}.npy")[:T].astype(np.float32) / 255
    lo = np.load(f"{OUT}/pairs/clip{cid}_L{lv}.npy").astype(np.float32) / 255
    return lo, gt


def unsharp(x, amt=1.0):
    return np.stack([np.clip(f + amt * (f - cv2.GaussianBlur(f, (0, 0), 1.5)), 0, 1) for f in x])


def bicubic(lo, lv):
    s = SCALE[lv]
    return np.stack([cv2.resize(f, (W, H), interpolation=cv2.INTER_CUBIC) for f in lo])


RESULTS = os.path.join(HERE, "results.json")


def evaluate(arms, test, levels, fn_for):
    """Mean over test clips of each metric, per (level, arm). Writes results.json; skips finished cells."""
    res = json.load(open(RESULTS)) if os.path.exists(RESULTS) else {}
    for lv in levels:
        for arm in arms:
            key = f"{lv}/{arm}"
            if key in res:
                continue
            ms = []
            for cid in test:
                lo, gt = load_pair(cid, lv)
                ms.append(metrics(fn_for(arm, lo, lv), gt))
            res[key] = {k: round(float(np.mean([m[k] for m in ms])), 4) for k in ms[0]}
            print(key, res[key], flush=True)
            json.dump(res, open(RESULTS, "w"), indent=2)
    return res


def baseline(test, levels):
    def fn(arm, lo, lv):
        up = bicubic(lo, lv)
        return up if arm == "bicubic" else unsharp(up, 1.0)
    return evaluate(["bicubic", "bicubic+unsharp1.0"], test, levels, fn)


def eval_cnn(test, levels, amounts=(0, 0.4, 0.6, 0.8, 1.0, 1.2), fmt="float"):
    """CNN (numpy reference, restore_math.restore) with unsharp amount 0 = plain CNN. Needs
    out/restorer_L{level}[_ternary].rstr from train_float.py --export."""
    import restore_math as rm
    tag = "" if fmt == "float" else "_" + fmt
    weights = {lv: rm.load(f"{OUT}/restorer_L{lv}{tag}.rstr") for lv in levels}

    def fn(arm, lo, lv):
        out = np.stack([rm.restore(f, weights[lv], SCALE[lv]) for f in lo])
        amt = float(arm.split("unsharp")[1])
        return unsharp(out, amt) if amt else out

    arm = "cnn" if fmt == "float" else fmt
    return evaluate([f"{arm}+unsharp{a}" for a in amounts], test, levels, fn)


def cbr(cid, kbps, arm):
    """One (arm, bitrate) cell on one clip, same stream budget for every arm. Full: 960x540 stream.
    Shrink: 540 level (480x270) stream, bicubic up. Restore: the same stream, float restorer + unsharp 1.0."""
    gt = np.load(f"{OUT}/gt/clip{cid}.npy")[:T].astype(np.float32) / 255
    if arm == "full":
        src = blur(clip_frames(cid))
    else:
        src = np.stack([cv2.resize(b, (W // SCALE["540"], H // SCALE["540"]), interpolation=cv2.INTER_AREA)
                        for b in blur(clip_frames(cid))])
    dec, kb = cbr_roundtrip(src, kbps)
    dec = dec.astype(np.float32) / 255
    if arm == "full":
        pred = dec
    elif arm == "shrink":
        pred = bicubic(dec, "540")
    else:
        import restore_math as rm
        w = rm.load(f"{OUT}/restorer_L540.rstr")
        pred = unsharp(np.stack([rm.restore(f, w, SCALE["540"]) for f in dec]), 1.0)
    return metrics(pred, gt), kb


def run_cbr(clips, kbps, arm):
    key = f"cbr{kbps}/{arm}"
    res = json.load(open(RESULTS)) if os.path.exists(RESULTS) else {}
    if key in res:
        print(key, "already done", flush=True)
        return
    rows = [cbr(cid, kbps, arm) for cid in clips]
    res[key] = {k: round(float(np.mean([m[k] for m, _ in rows])), 4) for k in rows[0][0]}
    res[key]["kbps_produced"] = round(float(np.mean([kb for _, kb in rows])), 1)
    print(key, res[key], flush=True)
    json.dump(res, open(RESULTS, "w"), indent=2)


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
    b = sub.add_parser("baseline")
    b.add_argument("--clips", default="0-3")
    b.add_argument("--levels", default="540,360")
    e = sub.add_parser("eval")
    e.add_argument("--clips", default="0-3")
    e.add_argument("--levels", default="540,360")
    e.add_argument("--format", default="float", choices=["float", "ternary"])
    c = sub.add_parser("cbr")
    c.add_argument("--clips", default="0-3")
    c.add_argument("--kbps", type=int, required=True)
    c.add_argument("--arm", required=True, choices=["full", "shrink", "restore"])
    a = ap.parse_args()
    if a.cmd == "cbr":
        run_cbr(list(ids(a.clips)), a.kbps, a.arm)
    elif a.cmd == "make":
        make(list(ids(a.clips)), a.levels.split(","))
    elif a.cmd == "baseline":
        baseline(list(ids(a.clips)), a.levels.split(","))
    elif a.cmd == "eval":
        eval_cnn(list(ids(a.clips)), a.levels.split(","), fmt=a.format)
