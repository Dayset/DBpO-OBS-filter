"""Field-packed interlace research bench (issue #19, step 1).

Compares, at a fixed CBR budget, what the viewer sees for:
  1080   plain 1080p stream (reference for "no change")
  540    real 960x540 stream upscaled (the starvation fallback today)
  field  one field per frame, packed into the top 1920x540 of a 1080p frame, black below,
         2-pixel marker bottom-left gives field parity; viewer weaves/deinterlaces to 1080p60

    python3 fieldpack_bench.py run SRC.mkv WORK KBPS [--encoder x264|nvenc] [--seconds N]

Metrics (luma, vs the 1080p source frames, first SETTLE frames skipped): PSNR and SSIM on the full
frame and centre, plus a temporal-error score (|frame_t - frame_t-1| error vs reference motion).
Static-scene overhead is measured by running a still clip through the same path.
"""
import argparse
import json
import os
import subprocess

import cv2
import numpy as np

W, H, FPS = 1920, 1080, 60
SETTLE = 30
CENTER = (slice(int(H * .35), int(H * .65)), slice(int(W * .35), int(W * .65)))
MARK_W, MARK_H = 16, 16          # marker box, bottom-left, MB-aligned
CRF_REF = 23


def read_frames(path, w, h, n):
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path, "-frames:v", str(n),
           "-vf", f"scale={w}:{h}:flags=lanczos,format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w)


def pack_fields(ref):
    """Even rows -> field 0, odd rows -> field 1; each field is 1920x540, placed top, black below."""
    n = ref.shape[0]
    out = np.zeros((n, H, W), np.uint8)
    for i in range(n):
        parity = i & 1
        field = ref[i, parity::2, :]                        # 540 x 1920 (line-skip, no filter)
        out[i, :540, :] = field
        out[i, 540 + 4 * 0:, :] = 0
        # marker: parity encoded as 2 blocks (white = 1, black = 0) in the bottom-left corner
        y0 = H - MARK_H
        out[i, y0:H, 0:MARK_W] = 255 if parity else 0
    return out


def encode(frames, out_path, kbps, encoder, fps=FPS):
    h, w = frames.shape[1], frames.shape[2]
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-x264-params",
           f"nal-hrd=cbr:force-cfr=1"] if encoder == "x264" else ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "cbr"]
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}",
           "-r", str(fps), "-i", "-", "-pix_fmt", "yuv420p", *enc,
           "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k", out_path]
    subprocess.run(cmd, input=np.ascontiguousarray(frames).tobytes(), check=True)
    return os.path.getsize(out_path) * 8 / (frames.shape[0] / fps) / 1000  # real kbps


def weave_deinterlace(packed):
    """Viewer side (simplified): read marker parity, take the field rows, motion-adaptive fill of the other."""
    n = packed.shape[0]
    out = np.zeros((n, H, W), np.uint8)
    for i in range(n):
        parity = 1 if packed[i, H - MARK_H + 8, 4] > 127 else 0
        field = packed[i, :540, :]
        out[i, parity::2, :] = field
        other = 1 - parity
        prev = out[i - 1] if i > 0 else field.repeat(2, 0)[:H]
        cur_interp = np.empty((540, W), np.float32)
        cur_interp[:] = field.astype(np.float32)
        # missing rows: average of neighbouring present rows, blended with previous frame when static
        up = np.roll(out[i, parity::2, :], 0, axis=0).astype(np.float32)
        spatial = (up + np.roll(up, -1, axis=0)) / 2
        temporal = prev[other::2, :].astype(np.float32)
        motion = np.abs(spatial - temporal) > 12
        out[i, other::2, :] = np.where(motion, spatial, temporal).astype(np.uint8)
    return out


def psnr(a, b):
    mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    return 99.0 if mse == 0 else 10 * np.log10(255 ** 2 / mse)


def ssim(a, b):
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    a, b = a.astype(np.float64), b.astype(np.float64)
    ma, mb = cv2.GaussianBlur(a, (11, 11), 1.5), cv2.GaussianBlur(b, (11, 11), 1.5)
    saa = cv2.GaussianBlur(a * a, (11, 11), 1.5) - ma ** 2
    sbb = cv2.GaussianBlur(b * b, (11, 11), 1.5) - mb ** 2
    sab = cv2.GaussianBlur(a * b, (11, 11), 1.5) - ma * mb
    return float(np.mean(((2 * ma * mb + c1) * (2 * sab + c2)) / ((ma ** 2 + mb ** 2 + c1) * (saa + sbb + c2))))


def score(dec, ref):
    s = slice(SETTLE, None)
    d, r = dec[s], ref[s]
    return {
        "psnr_full": float(np.mean([psnr(x, y) for x, y in zip(d, r)])),
        "psnr_center": float(np.mean([psnr(x[CENTER], y[CENTER]) for x, y in zip(d, r)])),
        "ssim_center": float(np.mean([ssim(x[CENTER], y[CENTER]) for x, y in zip(d, r)])),
        "temporal_err": float(np.mean(np.abs(np.diff(d.astype(np.int16), axis=0) - np.diff(r.astype(np.int16), axis=0)))),
    }


def decode(path, w, h, n):
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path, "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w)


def run(src, work, kbps, encoder, seconds):
    os.makedirs(work, exist_ok=True)
    n = seconds * FPS
    ref = read_frames(src, W, H, n)
    res = {"kbps": kbps, "encoder": encoder, "seconds": seconds, "variants": {}}

    v1080 = os.path.join(work, f"v1080_{kbps}_{encoder}.mkv")
    res["variants"]["1080"] = {"real_kbps": encode(ref, v1080, kbps, encoder)}
    res["variants"]["1080"].update(score(decode(v1080, W, H, n), ref))

    small = np.stack([cv2.resize(f, (960, 540), interpolation=cv2.INTER_AREA) for f in ref])
    v540 = os.path.join(work, f"v540_{kbps}_{encoder}.mkv")
    res["variants"]["540"] = {"real_kbps": encode(small, v540, kbps, encoder)}
    up = np.stack([cv2.resize(f, (W, H), interpolation=cv2.INTER_LINEAR) for f in decode(v540, 960, 540, n)])
    res["variants"]["540"].update(score(up, ref))

    packed = pack_fields(ref)
    vf = os.path.join(work, f"vfield_{kbps}_{encoder}.mkv")
    res["variants"]["field"] = {"real_kbps": encode(packed, vf, kbps, encoder)}
    dec = decode(vf, W, H, n)
    marker_ok = float(np.mean([(d[H - MARK_H + 8, 4] > 127) == (i & 1 == 1) for i, d in enumerate(dec)]))
    res["variants"]["field"]["marker_ok"] = marker_ok
    res["variants"]["field"].update(score(weave_deinterlace(dec), ref))

    with open(os.path.join(work, f"result_{kbps}_{encoder}.json"), "w") as f:
        json.dump(res, f, indent=2)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("src")
    ap.add_argument("work")
    ap.add_argument("kbps", type=int)
    ap.add_argument("--encoder", default="x264", choices=["x264", "nvenc"])
    ap.add_argument("--seconds", type=int, default=10)
    a = ap.parse_args()
    print(json.dumps(run(a.src, a.work, a.kbps, a.encoder, a.seconds), indent=2))
