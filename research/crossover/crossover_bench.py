"""Squish vs plain stream across bitrates, same clip and same frames at two output sizes (issue #33, gaps G1/G4).

Clip: PlanetSide 2 release v3.0.0 (sha256 f52627aed49a9e7f), decoded with
  ffmpeg -i Planetside2LeftSpin.mp4 -vf "fps=60,scale=W:H:flags=area" -pix_fmt rgb24 -f rawvideo ref.rgb
Each arm: x264 veryfast CBR (bufsize = 2 x rate, GOP 120), decode, bilinear unsquish (the viewer), luma PSNR/SSIM
against the reference on frames 300-599 step 2. Centre = the 30% x 30% 1:1 box. Squish = squish_math.Axis(c=0.15, linear).

  crossover_bench.py REF_RGB W H FIRST N_FRAMES OUT.json SQ_WxSQ_H[,..] KBPS[,..]
FIRST = index in REF_RGB of source frame FIRST_SRC given as FIRST=file_offset:source_frame. Resumes from OUT.json.
"""
import json, os, subprocess, sys
import numpy as np, cv2
from skimage.metrics import structural_similarity as ssim_fn
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "foveation"))
from squish_math import Axis

ref_path, W, H = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
OFF, SRC0 = map(int, sys.argv[4].split(":"))
N, out_json = int(sys.argv[5]), sys.argv[6]
SIZES = [tuple(map(int, s.split("x"))) for s in sys.argv[7].split(",")]
RATES = [int(k) for k in sys.argv[8].split(",")]
C = 0.15
ref = np.memmap(ref_path, np.uint8, "r", offset=OFF * H * W * 3, shape=(N, H, W, 3))
EVAL = [i for i in range(300 - SRC0, 600 - SRC0, 2)]
Y0, Y1, X0, X1 = int(H * (.5 - C)), int(H * (.5 + C)), int(W * (.5 - C)), int(W * (.5 + C))
LUMA = np.array([0.299, 0.587, 0.114], np.float32)

def mats(n, m):
    ax = Axis(r=m / n, c=C, f=0.5, p=1.0)
    ue = ax.forward(np.linspace(0, 1, n + 1)) * m
    j = np.arange(m)
    ov = np.clip(np.minimum((j + 1)[:, None], ue[None, 1:]) - np.maximum(j[:, None], ue[None, :-1]), 0, None)
    return (ov / ov.sum(1, keepdims=True)).astype(np.float32), (ax.forward((np.arange(n) + 0.5) / n) * m - 0.5).astype(np.float32)

def run(name, w, h, kbps, pre, post):
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", "60",
        "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize",
        f"{2 * kbps}k", "-g", "120", "-pix_fmt", "yuv420p", "-y", f"{out_json}.{name}.mkv"], stdin=subprocess.PIPE)
    for i in range(N):
        enc.stdin.write(np.ascontiguousarray(pre(ref[i])).tobytes())
    enc.stdin.close(); assert enc.wait() == 0
    mkv = f"{out_json}.{name}.mkv"
    bits = os.path.getsize(mkv) * 8 / (N / 60) / 1000
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", mkv, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    acc, want = [], set(EVAL)
    for i in range(N):
        b = dec.stdout.read(w * h * 3)
        if i not in want: continue
        p = post(np.frombuffer(b, np.uint8).reshape(h, w, 3)).astype(np.float32) @ LUMA
        g = ref[i].astype(np.float32) @ LUMA
        e = (p - g) ** 2
        s, smap = ssim_fn(p, g, data_range=255.0, full=True)
        acc.append((10 * np.log10(255 ** 2 / e.mean()), s, 10 * np.log10(255 ** 2 / e[Y0:Y1, X0:X1].mean()), smap[Y0:Y1, X0:X1].mean()))
    dec.wait(); os.remove(mkv)
    a = np.array(acc); m = a.mean(0)
    return {"kbps_actual": round(bits, 1), "psnr": round(float(m[0]), 3), "ssim": round(float(m[1]), 4),
            "c_psnr": round(float(m[2]), 3), "c_ssim": round(float(m[3]), 4), "psnr_sd": round(float(a[:, 0].std()), 3)}

res = json.load(open(out_json)) if os.path.exists(out_json) else {}
for k in RATES:
    arms = [("plain", W, H, lambda f: f, lambda f: f)]
    for sw, sh in SIZES:
        (AX, UX), (AY, UY) = mats(W, sw), mats(H, sh)
        MX, MY = np.tile(UX[None, :], (H, 1)), np.tile(UY[:, None], (1, W))
        def pre(f, AX=AX, AY=AY, sw=sw, sh=sh):
            t = (AX @ f.astype(np.float32).transpose(1, 0, 2).reshape(W, H * 3)).reshape(sw, H, 3).transpose(1, 0, 2)
            return np.clip((AY @ t.reshape(H, sw * 3)).reshape(sh, sw, 3) + 0.5, 0, 255).astype(np.uint8)
        arms.append((f"squish_{sw}x{sh}", sw, sh, pre, lambda f, MX=MX, MY=MY: cv2.remap(f, MX, MY, cv2.INTER_LINEAR)))
    for name, w, h, pre, post in arms:
        key = f"{k}/{name}"
        if key in res: continue
        res[key] = run(name, w, h, k, pre, post)
        json.dump(res, open(out_json, "w"), indent=1)
        print(key, res[key], flush=True)
