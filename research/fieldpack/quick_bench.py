"""Quick field-pack bench: the busiest N seconds of a clip, x264 veryfast CBR, approximate scores.

    python3 quick_bench.py SRC WORK [--seconds 4] [--kbps 6000,3000] [--encoder x264|nvenc]

Arms (all at the same CBR, same GOP 2 s, 2 B-frames):
    1080              plain stream
    720, 540          starvation levels today (plain low-res stream, viewer bilinear upscale)
    field0 / field1   field-packed 1920x540 in a 1080p frame, anti-twitter 0 / 1
                      viewers: dbpo (fieldpack_math.Viewer, what the extension ships),
                               bwdif (ffmpeg, modern reference), bob (spatial only, lower bound)
Metrics vs the 1080p60 source (luma BT.709): centre SSIM/PSNR (centre 30% x 30%), whole SSIM
(every 4th frame), temporal error (mean |d_view - d_ref| of frame-to-frame differences, centre).
Static overhead: a still frame for 2 s at CRF 23, 1080p vs field-packed.
Results: WORK/quick_results.json + a table on stdout. Resumable (finished arms are skipped).
"""
import argparse
import json
import os
import subprocess
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fieldpack_math as fm  # noqa: E402

W, H, FPS = 1920, 1080, 60
C = (slice(int(H * .35), int(H * .65)), slice(int(W * .35), int(W * .65)))


def busiest_start(src, seconds):
    p = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", src, "-vf", "fps=60,scale=240:135:flags=area,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    g = np.frombuffer(p, np.uint8).reshape(-1, 135, 240).astype(np.float32)
    d = np.abs(np.diff(g, axis=0)).mean((1, 2))
    n = seconds * FPS
    if len(d) <= n:
        return 0.0, d
    cs = np.concatenate([[0], np.cumsum(d)])
    k = int(np.argmax(cs[n:] - cs[:-n]))
    return k / FPS, d


def make_ref(src, work, seconds):
    ref = os.path.join(work, "ref.mkv")
    if not os.path.exists(ref):
        t0, d = busiest_start(src, seconds)
        print(f"busiest {seconds} s starts at {t0:.2f} s (mean temporal diff {d.mean():.2f})", flush=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t0:.3f}", "-i", src, "-t", str(seconds),
                        "-vf", "fps=60,scale=1920:1080:flags=area", "-an", "-c:v", "libx264", "-preset", "ultrafast",
                        "-qp", "0", "-pix_fmt", "yuv444p", ref], check=True)
    return ref


def frames(path, w=W, h=H, vf=None):
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    n = w * h * 3
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
    p.wait()


def enc_args(encoder, kbps):
    if encoder == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-multipass", "fullres", "-rc", "cbr",
                "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps}k", "-g", "120", "-bf", "2"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-x264-params", "nal-hrd=cbr", "-b:v", f"{kbps}k",
            "-maxrate", f"{kbps}k", "-bufsize", f"{kbps}k", "-g", "120", "-bf", "2"]


def encode(gen, w, h, out, args):
    p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                          "-r", str(FPS), "-i", "-", "-pix_fmt", "yuv420p", *args, out], stdin=subprocess.PIPE)
    n = 0
    for f in gen:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
        n += 1
    p.stdin.close()
    p.wait()
    return os.path.getsize(out) * 8 / (n / FPS) / 1000


def luma(f):
    f = f.astype(np.float32)
    return 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]


def ssim(a, b):
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    ma, mb = cv2.GaussianBlur(a, (7, 7), 1.5), cv2.GaussianBlur(b, (7, 7), 1.5)
    saa = cv2.GaussianBlur(a * a, (7, 7), 1.5) - ma * ma
    sbb = cv2.GaussianBlur(b * b, (7, 7), 1.5) - mb * mb
    sab = cv2.GaussianBlur(a * b, (7, 7), 1.5) - ma * mb
    return float(np.mean(((2 * ma * mb + c1) * (2 * sab + c2)) / ((ma * ma + mb * mb + c1) * (saa + sbb + c2))))


def psnr(a, b):
    m = float(np.mean((a - b) ** 2))
    return 99.0 if m == 0 else 10 * np.log10(255 * 255 / m)


def score(view_gen, ref):
    cs, cp, ws, te = [], [], [], []
    pv = pr = None
    for i, (v, r) in enumerate(zip(view_gen, frames(ref))):
        yv, yr = luma(v), luma(r)
        cs.append(ssim(yv[C], yr[C]))
        cp.append(psnr(yv[C], yr[C]))
        if i % 4 == 0:
            ws.append(ssim(yv, yr))
        if pv is not None:
            te.append(float(np.mean(np.abs((yv[C] - pv) - (yr[C] - pr)))))
        pv, pr = yv[C], yr[C]
    return {"centre_ssim": float(np.mean(cs)), "centre_psnr": float(np.mean(cp)), "whole_ssim": float(np.mean(ws)),
            "temporal_err": float(np.mean(te)), "frames": len(cs)}


def view_lowres(path, w, h):
    for f in frames(path, w, h):
        yield cv2.resize(f, (W, H), interpolation=cv2.INTER_LINEAR)


def split_gen(ref, lv):
    prev = None
    for i, f in enumerate(frames(ref)):
        prev = fm.pack_split(f, i & 1, prev, lv)
        yield prev


def view_split(path, how):
    if how in ("bwdif", "yadif"):
        vf = (f"crop=1920:540:0:'if(mod(n,2),540,0)',weave=first_field=top,setfield=tff,"
              f"{how}=mode=send_field:parity=tff:deint=all")
        yield from frames(path, vf=vf)
        return
    v = fm.Viewer(sensitivity=1.0, ela=True)
    for i, f in enumerate(frames(path)):
        if how == "bob":
            v.f1 = None
        yield fm.view_split(f, i & 1, v)


def view_field(path, how, sens=1.0):
    if how in ("bwdif", "yadif"):
        # top 540 rows = one field per frame, parity alternating from even; weave pairs, deinterlace per field.
        # Both look one field ahead (one frame of delay in a player).
        vf = f"crop=1920:540:0:0,weave=first_field=top,setfield=tff,{how}=mode=send_field:parity=tff:deint=all"
        yield from frames(path, vf=vf)
        return
    v = fm.Viewer(sensitivity=sens, ela=True)
    for i, f in enumerate(frames(path)):
        if how == "bob":
            v.f1 = None
        yield fm.view(f, i & 1, v)


def still_split(still, n):
    prev = None
    for i in range(n):
        prev = fm.pack_split(still, i & 1, prev, 1)
        yield prev


def static_overhead(ref, work):
    still = next(frames(ref))
    out = {}
    for name, gen in [("1080", (still for _ in range(120))),
                      ("field1", (fm.pack(still, i & 1, 1) for i in range(120))),
                      ("field0", (fm.pack(still, i & 1, 0) for i in range(120))),
                      ("split1", still_split(still, 120))]:
        path = os.path.join(work, f"static_{name}.mp4")
        out[name] = encode(gen, W, H, path, ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-g", "120",
                                             "-bf", "2"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("work")
    ap.add_argument("--seconds", type=int, default=4)
    ap.add_argument("--kbps", default="6000,3000")
    ap.add_argument("--encoder", default="x264", choices=["x264", "nvenc"])
    ap.add_argument("--arms", default="", help="comma list of arm name prefixes to run (default: all)")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    resf = os.path.join(a.work, "quick_results.json")
    res = json.load(open(resf)) if os.path.exists(resf) else {}

    def save():
        with open(resf + ".tmp", "w") as f:
            json.dump(res, f, indent=1)
        os.replace(resf + ".tmp", resf)

    ref = make_ref(a.src, a.work, a.seconds)
    if "split1" not in res.get("static_kbps", {}):
        res["static_kbps"] = static_overhead(ref, a.work)
        save()
    for kbps in [int(k) for k in a.kbps.split(",")]:
        args = enc_args(a.encoder, kbps)
        arms = [("vhalf", lambda p: encode((cv2.resize(f, (1920, 540), interpolation=cv2.INTER_AREA) for f in frames(ref)),
                                           1920, 540, p, args), lambda p: view_lowres(p, 1920, 540)),
                ("1080", lambda p: encode(frames(ref), W, H, p, args), lambda p: frames(p)),
                ("720", lambda p: encode((cv2.resize(f, (1280, 720), interpolation=cv2.INTER_AREA) for f in frames(ref)),
                                         1280, 720, p, args), lambda p: view_lowres(p, 1280, 720)),
                ("540", lambda p: encode((cv2.resize(f, (960, 540), interpolation=cv2.INTER_AREA) for f in frames(ref)),
                                         960, 540, p, args), lambda p: view_lowres(p, 960, 540))]
        for lvl in (0, 1):
            enc = (lambda lv: lambda p: encode((fm.pack(f, i & 1, lv) for i, f in enumerate(frames(ref))), W, H, p,
                                               args))(lvl)
            for how in ("dbpo", "bwdif", "yadif", "bob"):
                arms.append((f"field{lvl}_{how}", enc, (lambda hw: lambda p: view_field(p, hw))(how)))
        for lvl in (0, 1):
            enc = (lambda lv: lambda p: encode(split_gen(ref, lv), W, H, p, args))(lvl)
            for how in ("bwdif", "bob", "dbpo"):
                arms.append((f"split{lvl}_{how}", enc, (lambda hw: lambda p: view_split(p, hw))(how)))
        for name, enc, view in arms:
            key = f"{a.encoder}_{kbps}_{name}"
            if key in res or (a.arms and not any(name.startswith(x) for x in a.arms.split(","))):
                continue
            path = os.path.join(a.work, f"{a.encoder}_{kbps}_{name.split('_')[0]}.mp4")
            t = time.time()
            if not os.path.exists(path):
                real = enc(path)
            else:
                real = os.path.getsize(path) * 8 / (a.seconds) / 1000
            r = {"kbps": kbps, "arm": name, "real_kbps": real}
            r.update(score(view(path), ref))
            r["seconds"] = time.time() - t
            res[key] = r
            save()
            print(f"{key:28s} centre SSIM {r['centre_ssim']:.4f}  PSNR {r['centre_psnr']:.2f}  whole {r['whole_ssim']:.4f}"
                  f"  temporal {r['temporal_err']:.2f}  {real:.0f} kbps", flush=True)
    print("static CRF23 kbps:", res["static_kbps"])


if __name__ == "__main__":
    main()
