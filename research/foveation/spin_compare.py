"""Fast left spin at the real Twitch setup: plain 6000 kbps stream vs squished 6000 kbps stream + viewer restoration.

Setup mirrored from the streamer's config: game 2560x1440, stream 1920x1080 @ 60 fps, x264 veryfast, CBR (nal-hrd),
buffer = bitrate, keyframe every 2 s, 2 B-frames, BT.709 limited range (what OBS writes).
  base    OBS scales 1440p -> 1080p (bicubic) and streams it; the viewer's browser scales it back up.
  squish  Foveated Squish 75% x 75% (center 30%, linear) to exactly 1920x1080, streamed; the extension unsquishes
          (viewer step: spin_compare_viewer.py).
The "Clean the squeezed area" pre-filter of the plugin is not modelled.

Stages (cached under WORK):  python3 spin_compare.py WORK [kbps] [frames]
  1 ref.mkv         synthetic 1440p60 fast left spin (80 px/frame), lossless
  2 base.mp4, squish.mp4
  3 frames.json     10 random frames (seeded, after the rate control settled) + extracted pngs / lossless webm
"""
import json
import os
import subprocess
import sys

import numpy as np
from scipy import sparse
from scipy.ndimage import gaussian_filter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "plugins", "perceptual-color-reduction", "tests"))
import atlas_math as am  # noqa: E402
import bitrate_harness as bh  # noqa: E402

SW, SH = 2560, 1440  # game
OW, OH = int(os.environ.get("SPIN_OW", 1920)), int(os.environ.get("SPIN_OH", 1080))  # stream (963p: 1712x964, 4:2:0 needs even rows)
CFG = "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.7778"
SPEED = 80  # px per frame, turning left
COLOR = "colorspace=bt709:color_primaries=bt709:color_trc=bt709:color_range=tv"
X264 = ["-r", "60", "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-bf", "2", "-g", "120", "-keyint_min", "120",
        "-x264-params", "nal-hrd=cbr:force-cfr=1", "-pix_fmt", "yuv420p", "-colorspace", "bt709",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


def panorama():
    """Periodic terrain strip, 1500 x P."""
    w = 8192 + 600
    world = bh.make_world(seed=3, h=1500, w=w)
    p = 8192
    out = world[:, :p].copy()
    a = (np.arange(600) / 600)[None, :, None].astype(np.float32)
    out[:, :600] = world[:, :600] * a + world[:, p:p + 600] * (1 - a)
    return out


def hud(f):
    h, w, _ = f.shape
    f[h - 160:h - 54, 80:880] = f[h - 160:h - 54, 80:880] * 0.35 + 0.05  # health panel
    f[h - 140:h - 74, 107:667] = [0.85, 0.15, 0.12]
    for i in range(12):  # text
        f[80:112, w - 800 + i * 59:w - 768 + i * 59] = 0.95
    f[54:534, 54:534] = f[54:534, 54:534] * 0.5 + np.array([0.05, 0.1, 0.05])  # minimap
    f[h // 2 - 1:h // 2 + 1, w // 2 - 24:w // 2 - 6] = 1  # crosshair
    f[h // 2 - 1:h // 2 + 1, w // 2 + 6:w // 2 + 24] = 1
    f[h // 2 - 24:h // 2 - 6, w // 2 - 1:w // 2 + 1] = 1
    f[h // 2 + 6:h // 2 + 24, w // 2 - 1:w // 2 + 1] = 1
    return f


def ref_frames(n):
    pan = panorama()
    p = pan.shape[1]
    rng = np.random.default_rng(11)
    sky = bh.sky(SH // 3, SW)
    cols = np.arange(SW)
    for i in range(n):
        x0 = (-i * SPEED) % p  # camera turns left: the world slides right
        ground = pan[100:100 + SH - SH // 3][:, (x0 + cols) % p]
        f = np.concatenate([sky, ground], 0).copy()
        for sy, spd, col in ((800, 24, (0.75, 0.2, 0.15)), (1000, -19, (0.2, 0.3, 0.75))):  # players
            sx = int((300 + spd * i) % (SW - 200))
            f[sy:sy + 106, sx:sx + 186] = col
        f = hud(f)
        f = f + rng.normal(0, 1.5 / 255, f.shape).astype(np.float32)
        yield (np.clip(f, 0, 1) * 255 + 0.5).astype(np.uint8)


def ffmpeg_in(args, size, pix="rgb24"):
    return subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", pix, "-s", size, "-r", "60",
                             "-i", "-"] + args, stdin=subprocess.PIPE)


def read_frames(path, w, h, vf=None):
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path] + (["-vf", vf] if vf else []) + ["-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    n = w * h * 3
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
    p.wait()


def stage_ref(work, n):
    out = os.path.join(work, "ref.mkv")
    if os.path.exists(out + ".ok"):
        return out
    p = ffmpeg_in(["-c:v", "ffv1", "-level", "3", "-pix_fmt", "bgr0", out], f"{SW}x{SH}")
    for f in ref_frames(n):
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close()
    p.wait()
    open(out + ".ok", "w").close()
    return out


def sparse_mats():
    atlas = am.set_source(am.Atlas.parse(CFG), SW, SH)
    sq = atlas.squish
    rows = sparse.csr_matrix(am.area_matrix(sq.y, SH, OH).astype(np.float32))
    cols = sparse.csr_matrix(am.area_matrix(sq.x, SW, OW).astype(np.float32))
    return rows, cols


def squish(f, rows, cols):
    x = f.astype(np.float32) / 255
    t = rows @ x.reshape(SH, SW * 3)  # (OH, SW*3)
    t = t.reshape(OH, SW, 3).transpose(1, 0, 2).reshape(SW, OH * 3)
    t = (cols @ t).reshape(OW, OH, 3).transpose(1, 0, 2)
    return (np.clip(t, 0, 1) * 255 + 0.5).astype(np.uint8)


def stage_streams(work, kbps):
    rc = ["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps}k"]
    ref = os.path.join(work, "ref.mkv")
    base = os.path.join(work, "base.mp4")
    if not os.path.exists(base + ".ok"):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", ref, "-vf",
                        f"setpts=N/(60*TB),scale={OW}:{OH}:flags=bicubic:out_color_matrix=bt709:out_range=tv,format=yuv420p"] + X264 + rc + [base], check=True)
        open(base + ".ok", "w").close()
    sq = os.path.join(work, "squish.mp4")
    if not os.path.exists(sq + ".ok"):
        rows, cols = sparse_mats()
        p = ffmpeg_in(["-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"] + X264 + rc + [sq], f"{OW}x{OH}")
        for f in read_frames(ref, SW, SH):
            p.stdin.write(np.ascontiguousarray(squish(f, rows, cols)).tobytes())
        p.stdin.close()
        p.wait()
        open(sq + ".ok", "w").close()


def stage_pick(work, n):
    path = os.path.join(work, "frames.json")
    if os.path.exists(path):
        return json.load(open(path))
    rng = np.random.default_rng(2026)
    idx = sorted(int(i) for i in rng.choice(np.arange(60, n), 10, replace=False))
    sel = "+".join(f"eq(n\\,{i})" for i in idx)
    for name in ("base", "squish"):
        src = os.path.join(work, f"{name}.mp4")
        # exact decoded frames (YUV) as a 1 fps lossless webm the browser can play and seek
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", src, "-vf", f"select='{sel}',setpts=N/TB",
                        "-fps_mode", "passthrough", "-c:v", "libvpx-vp9", "-lossless", "1", "-pix_fmt", "yuv420p",
                        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
                        os.path.join(work, f"{name}_sel.webm")], check=True)
    info = {"frames": idx, "kbps": {}}
    for name in ("base", "squish"):
        info["kbps"][name] = round(os.path.getsize(os.path.join(work, f"{name}.mp4")) * 8 / (n / 60) / 1000)
    # keyframes among the picks
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "frame=pict_type", "-of", "csv=p=0",
                          os.path.join(work, "base.mp4")], capture_output=True, text=True).stdout.split()
    info["types"] = {str(i): out[i] for i in idx}
    json.dump(info, open(path, "w"), indent=1)
    return info


if __name__ == "__main__":
    work = sys.argv[1]
    kbps = int(sys.argv[2]) if len(sys.argv) > 2 else 6000
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 240
    os.makedirs(work, exist_ok=True)
    stage_ref(work, n)
    print("ref done", flush=True)
    stage_streams(work, kbps)
    print("streams done", flush=True)
    print(stage_pick(work, n))
