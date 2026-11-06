"""Starvation levels on a long real recording (issues #12, #13, #17).

    python3 clip_bench.py prep CLIP WORK              # 1080p60 lossless reference, 240x135 metric, CRF-23 demand per window
    python3 clip_bench.py run WORK KBPS [NAME ...]    # CBR encode + measure; NAME = s0 s1 s2 s3 (static level) | auto
    python3 clip_bench.py report WORK                 # tables from WORK/results.json

Reference = the clip scaled to 1920x1080 (area). Levels / marker / viewer = starvation_math.py (the spec).
Metrics: luma SSIM / PSNR vs the reference on every STEP-th frame, whole frame and centre 30%x30%, per 2 s window.
Encoder: x264 veryfast CBR (maxrate = bitrate, bufsize = bitrate), keyframe every 2 s, 2 B-frames.
"""
import json
import os
import subprocess
import sys

import cv2
import numpy as np
from scipy.ndimage import uniform_filter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import starvation_math as sm  # noqa: E402

W, H, FPS, WIN, STEP = 1920, 1080, 60, 120, 6
CENTER = (slice(int(H * .35), int(H * .65)), slice(int(W * .35), int(W * .65)))


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def frames(path, w=W, h=H, pix="rgb24", vf=None):
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path] + (["-vf", vf] if vf else []) + ["-f", "rawvideo", "-pix_fmt", pix, "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=1 << 24)
    n = w * h * (3 if pix == "rgb24" else 1)
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        yield np.frombuffer(b, np.uint8).reshape((h, w, 3) if pix == "rgb24" else (h, w))
    p.wait()


def frame_bits(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "packet=pts,size", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.split()
    pk = sorted((int(a), int(b)) for a, b in (ln.split(",")[:2] for ln in out))
    return np.array([s * 8 for _, s in pk], np.float64)


def prep(clip, work):
    os.makedirs(work, exist_ok=True)
    ref = os.path.join(work, "ref.mkv")
    if not os.path.exists(ref + ".ok"):
        sh(["ffmpeg", "-loglevel", "error", "-y", "-i", clip, "-an", "-vf", "scale=1920:1080:flags=area,fps=60", "-c:v", "libx264", "-preset", "ultrafast",
            "-qp", "0", "-pix_fmt", "yuv444p", ref])
        open(ref + ".ok", "w").close()
    met = os.path.join(work, "metric.npy")
    if not os.path.exists(met):
        prev, out = None, []
        for g in frames(ref, 240, 135, "gray", "scale=240:135:flags=area"):
            g = g.astype(np.float32)
            out.append(0.0 if prev is None else float(np.abs(g - prev).mean()))
            prev = g
        np.save(met, np.array(out))
    crf = os.path.join(work, "crf23.mp4")
    if not os.path.exists(crf + ".ok"):
        sh(["ffmpeg", "-loglevel", "error", "-y", "-i", ref, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-g", "120", "-bf", "2",
            "-pix_fmt", "yuv420p", crf])
        open(crf + ".ok", "w").close()
    m, bits = np.load(met), frame_bits(crf)
    n = min(len(m), len(bits)) // WIN * WIN
    bw = bits[:n].reshape(-1, WIN).sum(1) * FPS / WIN / 1e6
    tw = m[:n].reshape(-1, WIN).mean(1)
    k = float((bw * tw).sum() / (tw * tw).sum())  # least squares through the origin
    res = {"frames": len(m), "windows": n // WIN, "crf23_mbit_s": bw.round(2).tolist(), "temporal": tw.round(3).tolist(),
           "corr": round(float(np.corrcoef(bw, tw)[0, 1]), 4), "k_fit": round(k, 3),
           "rel_err": round(float(np.abs(k * tw - bw).mean() / bw.mean()), 3)}
    json.dump(res, open(os.path.join(work, "prep.json"), "w"))
    print(json.dumps({k: v for k, v in res.items() if k not in ("crf23_mbit_s", "temporal")}))
    print("crf23 Mbit/s per 2 s:", res["crf23_mbit_s"])


def level_for(name, i, sched):
    return sched[i] if name == "auto" else int(name[1])


def ssim_map(a, b):
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    ma, mb = uniform_filter(a, 7), uniform_filter(b, 7)
    va, vb = uniform_filter(a * a, 7) - ma * ma, uniform_filter(b * b, 7) - mb * mb
    cab = uniform_filter(a * b, 7) - ma * mb
    return ((2 * ma * mb + c1) * (2 * cab + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))


def luma(f):
    return f[..., 0] * np.float32(0.299) + f[..., 1] * np.float32(0.587) + f[..., 2] * np.float32(0.114)


def psnr(a, b):
    mse = float(((a - b) ** 2).mean())
    return 99.0 if mse < 1e-6 else 10 * np.log10(255 ** 2 / mse)


def run(work, kbps, names, k=None):
    ref = os.path.join(work, "ref.mkv")
    m = np.load(os.path.join(work, "metric.npy"))
    prep_j = json.load(open(os.path.join(work, "prep.json")))
    k = k or sm.K_DEFAULT
    resp = os.path.join(work, "results.json")
    allr = json.load(open(resp)) if os.path.exists(resp) else {}
    for name in names:
        key = f"{name}@{kbps}" + (f"@k{k}" if name == "auto" and k != sm.K_DEFAULT else "")
        if key in allr:
            continue
        sched = sm.schedule(m, kbps, k=k) if name == "auto" else None
        path = os.path.join(work, key.replace("@", "_") + ".mp4")
        n = len(m)
        if not os.path.exists(path + ".ok"):
            cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "60", "-i", "-",
                   "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps}k",
                   "-g", "120", "-bf", "2", "-pix_fmt", "yuv420p", path]
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            for i, f in enumerate(frames(ref)):
                p.stdin.write(sm.render(f, level_for(name, i, sched)).tobytes())
            p.stdin.close()
            p.wait()
            open(path + ".ok", "w").close()
        rows = []
        for i, (a, g) in enumerate(zip(frames(ref), frames(path))):
            if i % STEP:
                continue
            code = sm.read_marker(g)
            lv = level_for(name, i, sched)
            v = sm.view(g, code if code is not None else 0)
            la, lb = luma(a.astype(np.float32)), luma(v.astype(np.float32))
            s = ssim_map(la, lb)
            rows.append((i, lv, int(code == lv), psnr(la, lb), psnr(la[CENTER], lb[CENTER]), float(s.mean()), float(s[CENTER].mean())))
        bits = frame_bits(path)
        allr[key] = {"rows": rows, "mbit_s": round(bits.sum() / (len(bits) / FPS) / 1e6, 2),
                     "levels": None if sched is None else [int(x) for x in sched[::STEP]]}
        json.dump(allr, open(resp, "w"))
        r = np.array(rows)
        print(key, f"clip SSIM {r[:, 5].mean():.4f} centre SSIM {r[:, 6].mean():.4f} centre PSNR {r[:, 4].mean():.2f} marker {r[:, 2].mean():.4f}", flush=True)


def report(work):
    allr = json.load(open(os.path.join(work, "results.json")))
    pj = json.load(open(os.path.join(work, "prep.json")))
    per = WIN // STEP
    for kb in sorted({int(k.split("@")[1]) for k in allr}):
        stat = {k.split("@")[0]: np.array(v["rows"]) for k, v in allr.items() if k.endswith(f"@{kb}") and k.startswith("s")}
        auto = {k: np.array(v["rows"]) for k, v in allr.items() if k.startswith("auto") and k.split("@")[1] == str(kb)}
        if not stat:
            continue
        nwin = min(len(r) for r in stat.values()) // per
        print(f"\n== {kb} kbit/s ==  (SSIM centre / whole, mean over the clip; level 0=1080 1=720 2=540 3=360)")
        for n, r in sorted(stat.items()):
            print(f"  static {n}: centre {r[:, 6].mean():.4f} whole {r[:, 5].mean():.4f}")
        best = []
        for w in range(nwin):
            sc = {n: r[w * per:(w + 1) * per, 6].mean() for n, r in stat.items()}
            best.append(max(sc, key=sc.get))
        orc = np.mean([stat[b][w * per:(w + 1) * per, 6].mean() for w, b in enumerate(best)])
        orcw = np.mean([stat[b][w * per:(w + 1) * per, 5].mean() for w, b in enumerate(best)])
        print(f"  oracle per 2 s: centre {orc:.4f} whole {orcw:.4f}")
        for n, r in auto.items():
            agree = np.mean([int(stat and best[w] == "s%d" % int(round(np.median(r[w * per:(w + 1) * per, 1])))) for w in range(nwin)])
            print(f"  {n}: centre {r[:, 6].mean():.4f} whole {r[:, 5].mean():.4f}, agrees with oracle in {agree * 100:.0f}% of windows")
        print("  best static level per window (s0..s3):", "".join(b[1] for b in best))
        print("  demand/budget per window (CRF23 Mbit/s / budget):", [round(d * 1000 / kb, 1) for d in pj["crf23_mbit_s"][:nwin]])


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "prep":
        prep(sys.argv[2], sys.argv[3])
    elif c == "run":
        a = sys.argv[4:]
        kk = None
        if a and a[0].startswith("k="):
            kk, a = float(a[0][2:]), a[1:]
        run(sys.argv[2], int(sys.argv[3]), a or ["s0", "s1", "s2", "s3", "auto"], kk)
    elif c == "report":
        report(sys.argv[2])


def external(work, path, offset, name):
    """Measure a stream encoded elsewhere (e.g. OBS NVENC -> Twitch): path[j] is aligned with ref[j + offset]."""
    ref = os.path.join(work, "ref.mkv")
    rows = []
    import itertools
    r = itertools.islice(frames(ref), offset, None)
    for j, (a, g) in enumerate(zip(r, frames(path))):
        if j % STEP:
            continue
        la, lb = luma(a.astype(np.float32)), luma(g.astype(np.float32))
        s = ssim_map(la, lb)
        rows.append((j + offset, 0, 1, psnr(la, lb), psnr(la[CENTER], lb[CENTER]), float(s.mean()), float(s[CENTER].mean())))
    resp = os.path.join(work, "results.json")
    allr = json.load(open(resp)) if os.path.exists(resp) else {}
    bits = frame_bits(path)
    allr[name] = {"rows": rows, "mbit_s": round(bits.sum() / (len(bits) / FPS) / 1e6, 2), "offset": offset}
    json.dump(allr, open(resp, "w"))
    r = np.array(rows)
    print(name, f"clip SSIM {r[:, 5].mean():.4f} centre SSIM {r[:, 6].mean():.4f} centre PSNR {r[:, 4].mean():.2f} whole PSNR {r[:, 3].mean():.2f}")


if __name__ == "__main__" and sys.argv[1] == "external":
    external(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5])
