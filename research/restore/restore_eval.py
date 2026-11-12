"""Matched CBR test on source-class clips, for the user's PC (issue #23.5, epic #20).

    python restore_eval.py --sources sources --out results_restore              # all classes, both encoders
    python restore_eval.py --sources sources --out results_restore --encoders x264 --seconds 2   # smoke test

Layout: sources/<class>/<clip>.mkv (or .mp4), 1920x1080, 60 fps. The folder name is the source class,
as in research/sweep. Each clip gives the first --seconds of picture (default 20 s), the reference is the
clip itself at 1920x1080.

For every clip, encoder (x264 veryfast, NVENC P7 CBR), bitrate (3000, 6000 kbit/s) and level (1080, 540, 360)
it writes the stream, decodes it and scores three arms against the reference (same metrics as ps2_bench):
  stream (1080 only), bicubic (540 / 360 upscale), restore (fine-tuned PS2 restorer), restore+unsharp1.0.
Measured kbit/s is recorded for every cell. Results are appended to --out/results.json after each cell, so
an interrupted run resumes: finished cells are skipped. Weights: weights/restorer_ps2ft_L{540,360}.rstr.
"""
import argparse
import glob
import json
import os
import subprocess
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "foveation"))
import restore_math as rm  # noqa: E402
import ps2_bench as pb  # noqa: E402  (score, unsharp, SIZES, SCALE)

W, H, FPS = 1920, 1080, 60
BITRATES = [3000, 6000]
LEVELS = ["1080", "540", "360"]
ENCODERS = {
    "x264": ["-c:v", "libx264", "-preset", "veryfast"],
    "nvenc": ["-c:v", "h264_nvenc", "-preset", "p7", "-rc", "cbr"],
}


def pipe_frames(cmd, w, h):
    """Yield uint8 RGB frames from an ffmpeg command that writes raw rgb24 to stdout (streamed, not held)."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    while True:
        b = p.stdout.read(w * h * 3)
        if len(b) < w * h * 3:
            break
        yield np.frombuffer(b, np.uint8).reshape(h, w, 3).copy()
    p.wait()


def clip_frames(path, seconds):
    n = int(seconds * FPS)
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path, "-frames:v", str(n), "-r", str(FPS), "-s", f"{W}x{H}",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    return pipe_frames(cmd, W, H)


def encode(frames, w, h, kbps, enc, out):
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
           "-r", str(FPS), "-i", "-"] + ENCODERS[enc] + [
           "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps}k", "-g", "120", "-bf", "2",
           "-pix_fmt", "yuv420p", out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = 0
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
        n += 1
    p.stdin.close()
    p.wait()
    if p.returncode:
        raise SystemExit(f"{enc} encode failed (is NVENC available on this PC?)")
    return os.path.getsize(out) * 8 / (n / FPS) / 1000


def cell(clip_key, path, seconds, gt, kbps, enc, level, weights, out_dir, results):
    """One (encoder, bitrate, level) cell on one clip. gt: {frame index: 1080p reference} for the sample."""
    w, h = pb.SIZES[level]
    tmp = os.path.join(out_dir, "tmp_cell.mkv")
    src = clip_frames(path, seconds)
    if level != "1080":
        src = (cv2.resize(f, (w, h), interpolation=cv2.INTER_AREA) for f in src)
    kb = encode(src, w, h, kbps, enc, tmp)
    rows = {}
    for i, lo in enumerate(pipe_frames(["ffmpeg", "-loglevel", "error", "-i", tmp, "-f", "rawvideo", "-pix_fmt",
                                        "rgb24", "-"], w, h)):
        if i not in gt:
            continue
        ref = gt[i].astype(np.float32)
        if level == "1080":
            arms = {"stream": lo.astype(np.float32)}
        else:
            rest = rm.restore(lo.astype(np.float32) / 255, weights[level], pb.SCALE[level]) * 255
            arms = {"bicubic": cv2.resize(lo, (W, H), interpolation=cv2.INTER_CUBIC).astype(np.float32),
                    "restore": np.clip(rest, 0, 255),
                    "restore+unsharp1.0": pb.unsharp(rest / 255, 1.0) * 255}
        for arm, pred in arms.items():
            rows.setdefault(arm, []).append(pb.score(pred, ref))
    os.remove(tmp)
    for arm, ms in rows.items():
        key = f"{clip_key}/{kbps}/{level}/{arm}"
        results[key] = {k: round(float(np.mean([m[k] for m in ms])), 4) for k in ms[0]}
        results[key]["kbps_measured"] = round(kb, 1)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--encoders", default="x264,nvenc")
    ap.add_argument("--seconds", type=float, default=20)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    res_path = os.path.join(a.out, "results.json")
    weights = {lv: rm.load(os.path.join(HERE, "weights", f"restorer_ps2ft_L{lv}.rstr")) for lv in ("540", "360")}
    clips = sorted(glob.glob(os.path.join(a.sources, "*", "*.mkv")) + glob.glob(os.path.join(a.sources, "*", "*.mp4")))
    if not clips:
        raise SystemExit(f"No clips in {a.sources}\\<class>\\ (see README.md)")
    for path in clips:
        cls = os.path.basename(os.path.dirname(path))
        clip_key = f"{cls}/{os.path.splitext(os.path.basename(path))[0]}"
        gt = {i: f for i, f in enumerate(clip_frames(path, a.seconds)) if i % 20 == 0}  # 60 sampled references
        for enc in a.encoders.split(","):
            for kbps in BITRATES:
                for level in LEVELS:
                    res = json.load(open(res_path)) if os.path.exists(res_path) else {}
                    arm = "stream" if level == "1080" else "restore+unsharp1.0"
                    key_enc = f"{enc}/{clip_key}/{kbps}/{level}/{arm}"
                    if key_enc in res:
                        continue
                    res = cell(f"{enc}/{clip_key}", path, a.seconds, gt, kbps, enc, level, weights, a.out, res)
                    json.dump(res, open(res_path, "w"), indent=2)
                    print(key_enc, "done", flush=True)


if __name__ == "__main__":
    main()
