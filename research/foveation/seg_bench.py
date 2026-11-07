"""20 s comparison on the busiest part of a real recording (issue #13 / #17, fast enough to run in the foreground).

    python3 seg_bench.py cut  REF.mkv START_FRAME SEG.mkv      # 1200 frames, lossless
    python3 seg_bench.py run  SEG.mkv WORK KBPS NAME           # NAME: full | a<step> | auto | abrupt | ramp
    python3 seg_bench.py ext  SEG.mkv WORK STREAM.mp4 OFFSET NAME   # a stream encoded elsewhere, STREAM[j] = SEG[j - OFFSET]
    python3 seg_bench.py stills SEG.mkv WORK FRAME NAME [NAME ...]  # side-by-side PNG of one frame as viewers see it

Stream: x264 veryfast CBR, keyframe every 2 s, 2 B-frames, 1920x1080 frame; picture top-left at step a, black
elsewhere, marker v2 (starvation_math). Viewer = crop + bilinear upscale (what the extension does with r = 1).
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

W, H, FPS, N, STEP = 1920, 1080, 60, 1200, 4
CENTER = (slice(int(H * .35), int(H * .65)), slice(int(W * .35), int(W * .65)))


def frames(path, skip=0):
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=1 << 24)
    n = W * H * 3
    i = 0
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        if i >= skip:
            yield np.frombuffer(b, np.uint8).reshape(H, W, 3)
        i += 1
    p.kill()


def render(f, a):
    out = np.zeros_like(f)
    if a == 0:
        out[:] = f
    else:
        rw, rh = sm.rect2(a, W, H)
        out[:rh, :rw] = cv2.resize(f, (rw, rh), interpolation=cv2.INTER_AREA)
    return sm.draw_marker2(out, a, 0)


def view(g, a):
    if a == 0:
        return g
    rw, rh = sm.rect2(a, W, H)
    return cv2.resize(g[:rh, :rw], (W, H), interpolation=cv2.INTER_LINEAR)


def schedule(name, seg, kbps):
    if name == "full":
        return [0] * N
    if name.startswith("a"):
        return [int(name[1:])] * N
    if name in ("abrupt", "ramp"):  # 1080 / 540 alternating every 5 s
        tgt = [0 if (i // 300) % 2 == 0 else 38 for i in range(N)]
        if name == "abrupt":
            return tgt
        r, out = sm.Ramp(FPS), []
        for t in tgt:
            r.go(t, 0, 2.0)
            out.append(r.step()[0])
        return out
    if name == "auto":  # detector + ramps (0.5 s down, 2 s up), like the filter
        prev, c, r, out = None, sm.Controller(kbps), sm.Ramp(FPS), []
        for f in frames(seg):
            m = sm.metric_frame(f)
            idx = c.step(0.0 if prev is None else sm.temporal(prev, m))
            prev = m
            ta = sm.a_for(sm.TARGET_FRACTIONS[idx])
            r.go(ta, 0, 0.5 if ta > round(r.a) else 2.0)
            out.append(r.step()[0])
        return [0] + out[:-1]  # decided one frame late
    raise ValueError(name)


def luma(f):
    f = f.astype(np.float32)
    return f[..., 0] * 0.299 + f[..., 1] * 0.587 + f[..., 2] * 0.114


def ssim_map(a, b):
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    ma, mb = uniform_filter(a, 7), uniform_filter(b, 7)
    va, vb = uniform_filter(a * a, 7) - ma * ma, uniform_filter(b * b, 7) - mb * mb
    cab = uniform_filter(a * b, 7) - ma * mb
    return ((2 * ma * mb + c1) * (2 * cab + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))


def psnr(a, b):
    return 10 * np.log10(255 ** 2 / max(float(((a - b) ** 2).mean()), 1e-6))


def bits(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "packet=pts,size", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.split()
    return sorted((int(a), int(b) * 8) for a, b in (ln.split(",")[:2] for ln in out))


def measure(seg, stream, sched, skip=0):
    rows = []
    for i, (a, g) in enumerate(zip(frames(seg), frames(stream, skip))):
        if i % STEP:
            continue
        st = sm.read_state(g) if sched is not None else None
        lv = sched[i] if sched is not None else 0
        v = view(g, lv)
        la, lb = luma(a), luma(v)
        s = ssim_map(la, lb)
        rows.append([i, lv, int(sched is None or st == (lv, 0)), psnr(la[CENTER], lb[CENTER]), float(s[CENTER].mean()),
                     psnr(la, lb), float(s.mean())])
    return rows


def save(work, key, res):
    p = os.path.join(work, "seg_results.json")
    allr = json.load(open(p)) if os.path.exists(p) else {}
    allr[key] = res
    json.dump(allr, open(p, "w"))
    r = np.array(res["rows"])
    print(f"{key}: centre SSIM {r[:, 4].mean():.4f} PSNR {r[:, 3].mean():.2f} | whole SSIM {r[:, 6].mean():.4f} "
          f"PSNR {r[:, 5].mean():.2f} | {res['mbit_s']} Mbit/s | marker {r[:, 2].mean() * 100:.1f}%", flush=True)


def run(seg, work, kbps, name):
    sched = schedule(name, seg, kbps)
    path = os.path.join(work, f"{name}_{kbps}.mp4")
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps}k",
           "-g", "120", "-bf", "2", "-pix_fmt", "yuv420p", path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i, f in enumerate(frames(seg)):
        p.stdin.write(render(f, sched[i]).tobytes())
    p.stdin.close()
    p.wait()
    b = bits(path)
    save(work, f"{name}@{kbps}", {"rows": measure(seg, path, sched), "sched": sched[::STEP],
                                  "mbit_s": round(sum(x for _, x in b) / (len(b) / FPS) / 1e6, 2)})


def ext(seg, work, stream, offset, name):
    b = bits(stream)
    rows = measure(seg, stream, None, skip=offset)
    save(work, name, {"rows": rows, "sched": None, "mbit_s": round(sum(x for _, x in b) / (len(b) / FPS) / 1e6, 2)})


def stills(seg, work, frame, names):
    """One frame from each variant as the viewer sees it, centre crop and whole frame, labelled."""
    allr = json.load(open(os.path.join(work, "seg_results.json")))
    ref = next(f for i, f in enumerate(frames(seg)) if i == frame)
    tiles = [("original", ref)]
    for n in names:
        key = n
        src = os.path.join(work, n.replace("@", "_") + ".mp4") if "@" in n else None
        if n == "twitch":
            g = next(f for i, f in enumerate(frames(os.path.join(work, "tw_seg.mp4"))) if i == frame)
            tiles.append(("Twitch today (NVENC 6000)", g))
            continue
        sched = allr[key]["sched"]
        lv = sched[frame // STEP]
        g = next(f for i, f in enumerate(frames(src)) if i == frame)
        tiles.append((f"{n} (picture {sm.rect2(lv, W, H)[1]}p)", view(g, lv)))
    crop = (slice(int(H * .3), int(H * .7)), slice(int(W * .3), int(W * .7)))
    for kind, sl in (("centre", crop), ("whole", (slice(None), slice(None)))):
        row = []
        for label, img in tiles:
            t = np.ascontiguousarray(img[sl])
            t = cv2.resize(t, (960, int(960 * t.shape[0] / t.shape[1])), interpolation=cv2.INTER_AREA if kind == "whole" else cv2.INTER_NEAREST)
            t = cv2.copyMakeBorder(t, 34, 0, 0, 0, cv2.BORDER_CONSTANT, value=(20, 20, 20))
            cv2.putText(t, label, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
            row.append(t)
        grid = [np.hstack(row[i:i + 2]) if i + 1 < len(row) else np.hstack([row[i], np.zeros_like(row[i])]) for i in range(0, len(row), 2)]
        out = os.path.join(work, f"still_{frame}_{kind}.jpg")
        cv2.imwrite(out, cv2.cvtColor(np.vstack(grid), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
        print(out)


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "cut":
        start = int(sys.argv[3])
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{start / FPS:.4f}", "-i", sys.argv[2], "-frames:v", str(N),
                        "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv444p", sys.argv[4]], check=True)
    elif c == "run":
        run(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5])
    elif c == "ext":
        ext(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), sys.argv[6])
    elif c == "stills":
        stills(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5:])
