"""Real-footage comparison: squish (+ sharp regions) at 963p vs a plain 1080p60 baseline, same bitrate.

Everything is cut into stages that cache their output, so a stage that dies (timeout, OOM) is
simply started again and carries on from the files it already wrote.

    python3 real_footage_bench.py ref.mkv work_dir [kbps]      # ref.mkv: 1920x1080 60 fps lossless

Variants (all x264 veryfast CBR at the same kbps, 60 fps):
  base      1080p, nothing done (what the stream looks like today)
  squish    1712x964, squished picture fills the frame, unsquished by the viewer
  atlas     1712x964, squished picture in an 80% box + sharp-region strip, unsquished by the viewer
  squish_hi like squish but at 3x the bitrate (how far more bits would go)
Metrics vs the 1080p reference as seen by the viewer (frames 30..end): PSNR, SSIM (luma), the
same inside the sharp regions, and a temporal noise score = mean |change of error between
consecutive frames| (flicker / shimmer that the codec adds; 0 = error is perfectly steady).
"""
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atlas_math as am  # noqa: E402

W, H = 1920, 1080
OW, OH = 1712, 964  # 963 rows would be odd, which 4:2:0 cannot encode
PLAIN = "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778"
ATLAS = ("FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778,ox=0,oy=0,bw=0.800,bh=1,"
         "gx=0.800,gy=0.850,gz=1.000,gv=1.000,gs=1.000,hx=0.010,hy=0.610,hz=0.210,hv=0.980,hs=1.000")
SETTLE = 30  # frames the rate control needs before the numbers mean anything


def frames(path, w, h):
    p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE)
    n = w * h * 3
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
    p.wait()


def encode(frames_iter, w, h, kbps, out):
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", "60",
           "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k",
           "-bufsize", f"{kbps}k", "-g", "120", "-bf", "2", "-pix_fmt", "yuv420p", out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames_iter:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close()
    p.wait()


def u8(f):
    return (np.clip(f, 0, 1) * 255 + 0.5).astype(np.uint8)


def stage_encode(name, ref, work, kbps):
    out = os.path.join(work, f"{name}.mp4")
    if os.path.exists(out + ".ok"):
        return out
    if name == "base":
        gen = (np.ascontiguousarray(f) for f in frames(ref, W, H))
        encode(gen, W, H, kbps, out)
    else:
        cfg = ATLAS if name == "atlas" else PLAIN
        box = name == "atlas"

        def gen():
            for f in frames(ref, W, H):
                src = f.astype(np.float32) / 255
                fr, _ = am.squish_frame_with_atlas(src, cfg, OW, OH, blank_duplicate=box)
                yield u8(fr)

        encode(gen(), OW, OH, kbps * 3 if name == "squish_hi" else kbps, out)
    open(out + ".ok", "w").close()
    return out


def stage_view(name, work):
    """Decode the stream and let the viewer unfold it; cache as lossless video."""
    src = os.path.join(work, f"{name}.mp4")
    out = os.path.join(work, f"{name}_view.mkv")
    if os.path.exists(out + ".ok"):
        return out
    if name == "base":
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", src, "-c:v", "ffv1", "-pix_fmt", "yuv444p", out],
                       check=True)
    else:
        cfg = ATLAS if name == "atlas" else PLAIN

        def gen():
            for f in frames(src, OW, OH):
                v = am.unsquish_with_atlas(f.astype(np.float32) / 255, cfg, W, H, W, H)
                yield u8(v)

        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
               "-r", "60", "-i", "-", "-c:v", "ffv1", "-pix_fmt", "yuv444p", out]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for v in gen():
            p.stdin.write(np.ascontiguousarray(v).tobytes())
        p.stdin.close()
        p.wait()
    open(out + ".ok", "w").close()
    return out


def luma(f):
    return (0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]).astype(np.float64)


def ssim(a, b):
    from scipy.ndimage import uniform_filter
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a, mu_b = uniform_filter(a, 7), uniform_filter(b, 7)
    va = uniform_filter(a * a, 7) - mu_a ** 2
    vb = uniform_filter(b * b, 7) - mu_b ** 2
    cab = uniform_filter(a * b, 7) - mu_a * mu_b
    return float((((2 * mu_a * mu_b + c1) * (2 * cab + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2))).mean())


def psnr(a, b):
    mse = ((a - b) ** 2).mean()
    return 99.0 if mse < 1e-9 else float(10 * np.log10(255 ** 2 / mse))


def measure(name, ref, work):
    view = stage_view(name, work)
    regs = am.Atlas.parse(ATLAS).regions
    masks = []
    for r in regs:
        masks.append((slice(int(r.y0 * H), int(r.y1 * H)), slice(int(r.x0 * W), int(r.x1 * W))))
    ps, ss, pr, noise, prev_err = [], [], [[] for _ in masks], [], None
    for i, (a, b) in enumerate(zip(frames(ref, W, H), frames(view, W, H))):
        if i < SETTLE:
            continue
        la, lb = luma(a), luma(b)
        ps.append(psnr(la, lb))
        ss.append(ssim(la, lb))
        for k, m in enumerate(masks):
            pr[k].append(psnr(la[m], lb[m]))
        err = lb - la
        if prev_err is not None:
            noise.append(float(np.abs(err - prev_err).mean()))
        prev_err = err
    size = os.path.getsize(os.path.join(work, f"{name}.mp4"))
    return {"psnr": round(float(np.mean(ps)), 2), "ssim": round(float(np.mean(ss)), 4),
            "region_psnr": [round(float(np.mean(x)), 2) for x in pr],
            "noise": round(float(np.mean(noise)), 3), "mbit_s": round(size * 8 / 2 / 1e6, 2)}


def main():
    ref, work = sys.argv[1], sys.argv[2]
    kbps = int(sys.argv[3]) if len(sys.argv) > 3 else 6000
    os.makedirs(work, exist_ok=True)
    res_path = os.path.join(work, "results.json")
    res = json.load(open(res_path)) if os.path.exists(res_path) else {}
    for name in ("base", "squish", "atlas", "squish_hi"):
        if name in res:
            continue
        stage_encode(name, ref, work, kbps)
        res[name] = measure(name, ref, work)
        json.dump(res, open(res_path, "w"), indent=1)
        print(name, res[name], flush=True)


if __name__ == "__main__":
    main()
