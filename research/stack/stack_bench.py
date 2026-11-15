"""Platform-stack measurements for issue #33: squish vs encoder ROI, server-side squish at transcode, dynamic use.

Everything runs from a lossless reference (x264 qp 0, yuv420p) made once per clip. Each arm is one x264 veryfast CBR
encode (bufsize 2 x rate, IDR every 120 frames), decoded, brought back to the reference size the way a viewer would
(bilinear unsquish, or bicubic upscale for a plain lower-resolution rendition) and scored per frame:
  luma PSNR whole frame and in the 30% x 30% centre box, and VMAF v0.6.1 (ffmpeg build with libvmaf, $VMAF_FFMPEG).
Encoder CPU time is ffmpeg -benchmark (user + system) for the encode minus a decode-only pass of the same input.

  stack_bench.py ref    SRC OUT.mkv W H [SS DUR]          lossless reference (fps=60, area scale)
  stack_bench.py arm    REF.mkv WORK OUT.json NAME KBPS KIND [ARGS]
      KIND = plain | squish SWxSH [ROI] | squishfast SWxSH | squishbase BWxBH SWxSH [ROI] | scale SWxSH | roi "x264 addroi chain"
      (squishfast: point-sampled squish, one bilinear lookup per output pixel)
      (squishbase: area-downscale to BWxBH first, squish that to SWxSH; the viewer unsquishes, then upscales bicubic)
  stack_bench.py motion REF.mkv OUT.npy                    240x135 temporal luma difference per frame (the detector)
$SCORE_REF scores against another reference than the encode input (server-side test: encode from a lossy ingest,
score against the pristine source); $KEEP keeps the encoded file; $UNSQ_CUBIC makes the viewer's unsquish bicubic instead of bilinear; $CODEC = x264 | x265 | svtav1.
Arms resume: an arm already in OUT.json is skipped. Per-frame arrays go to WORK/<NAME>_<KBPS>.npz.
"""
import json, os, re, subprocess, sys, time
import numpy as np, cv2
from scipy import sparse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "foveation"))
from squish_math import Axis

FF, VFF, C = "ffmpeg", os.environ.get("VMAF_FFMPEG", "ffmpeg"), 0.15

def probe(path):
    o = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip().split(",")
    return int(o[0]), int(o[1])

def frames(path, w, h, vf=None):
    cmd = [FF, "-v", "error", "-i", path] + (["-vf", vf] if vf else []) + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=w * h * 3)
    while True:
        b = p.stdout.read(w * h * 3)
        if len(b) < w * h * 3: break
        yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
    p.wait()

def lossless_writer(w, h, out):
    return subprocess.Popen([FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", "60",
        "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)

class Squish:
    """Area-filtered separable squish (squish_math.Axis, centre 30%, linear) and the viewer's bilinear unsquish."""
    def __init__(s, W, H, sw, sh):
        s.W, s.H, s.sw, s.sh = W, H, sw, sh
        (s.AX, ux), (s.AY, uy) = s.axis(W, sw), s.axis(H, sh)
        s.MX, s.MY = np.tile(ux[None, :], (H, 1)), np.tile(uy[:, None], (1, W))
    @staticmethod
    def axis(n, m):
        ax = Axis(r=m / n, c=C, f=0.5, p=1.0)
        ue = ax.forward(np.linspace(0, 1, n + 1)) * m
        j = np.arange(m)
        ov = np.clip(np.minimum((j + 1)[:, None], ue[None, 1:]) - np.maximum(j[:, None], ue[None, :-1]), 0, None)
        return sparse.csr_matrix((ov / ov.sum(1, keepdims=True)).astype(np.float32)), \
               (ax.forward((np.arange(n) + 0.5) / n) * m - 0.5).astype(np.float32)
    def fwd(s, f):
        a = s.AX @ np.ascontiguousarray(f.astype(np.float32).transpose(1, 0, 2)).reshape(s.W, -1)
        a = s.AY @ np.ascontiguousarray(a.reshape(s.sw, s.H, 3).transpose(1, 0, 2)).reshape(s.H, -1)
        return np.clip(a.reshape(s.sh, s.sw, 3) + 0.5, 0, 255).astype(np.uint8)
    def inv(s, f):
        return cv2.remap(f, s.MX, s.MY, cv2.INTER_CUBIC if os.environ.get("UNSQ_CUBIC") else cv2.INTER_LINEAR)
    def fast(s, f):
        """Point-sampled squish: one bilinear lookup per output pixel, no area filter (what a cheap shader does)."""
        if not hasattr(s, "FX"):
            def src(n, m):
                ax = Axis(r=m / n, c=C, f=0.5, p=1.0); u = np.linspace(0, 1, 40001)
                return (np.interp((np.arange(m) + 0.5) / m, ax.forward(u), u) * n - 0.5).astype(np.float32)
            s.FX, s.FY = np.tile(src(s.W, s.sw)[None, :], (s.sh, 1)), np.tile(src(s.H, s.sh)[:, None], (1, s.sw))
        return cv2.remap(f, s.FX, s.FY, cv2.INTER_LINEAR)

def bench(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
    m = re.search(r"utime=([\d.]+)s stime=([\d.]+)s rtime=([\d.]+)s", r.stderr)
    return float(m.group(1)) + float(m.group(2))

def encode(src, out, kbps, vf=None):
    rate = ["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k", "-g", "120", "-pix_fmt", "yuv420p"]
    codec = {"x264": ["-c:v", "libx264", "-preset", "veryfast", "-keyint_min", "120", "-sc_threshold", "0"],
             "x265": ["-c:v", "libx265", "-preset", "veryfast", "-x265-params", "keyint=120:min-keyint=120:scenecut=0:log-level=error"],
             "svtav1": ["-c:v", "libsvtav1", "-preset", "10"]}[os.environ.get("CODEC", "x264")]  # $CODEC picks the encoder
    if os.environ.get("CODEC") == "svtav1":  # SVT-AV1 refuses CBR outside low-delay mode: target-bitrate VBR
        rate = ["-b:v", f"{kbps}k", "-g", "120", "-pix_fmt", "yuv420p"]
    cpu = bench([FF, "-nostats", "-benchmark", "-y", "-i", src] + (["-vf", vf] if vf else []) + codec + rate + [out])
    return cpu - bench([FF, "-nostats", "-benchmark", "-i", src, "-f", "null", "-"])

def packets(path):
    o = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts,size", "-of",
                        "csv=p=0", path], capture_output=True, text=True).stdout.split()
    rows = sorted((int(a), int(b)) for a, b in (l.split(",")[:2] for l in o))
    return np.array([b for _, b in rows])

def score(dist, ref, W, H, dw, dh, post, log):
    vm = subprocess.Popen([VFF, "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "60", "-i",
        "-", "-r", "60", "-i", ref, "-lavfi", f"[0:v]format=yuv420p[d];[1:v]format=yuv420p[r];[d][r]libvmaf=log_fmt=json:"
        f"log_path={log}:n_threads=2", "-f", "null", "-"], stdin=subprocess.PIPE)
    y0, y1, x0, x1 = int(H * (.5 - C)), int(H * (.5 + C)), int(W * (.5 - C)), int(W * (.5 + C))
    ps, cs = [], []
    for d, g in zip(frames(dist, dw, dh), frames(ref, W, H)):
        p = post(d)
        e = (cv2.cvtColor(p, cv2.COLOR_RGB2GRAY).astype(np.float32) - cv2.cvtColor(g, cv2.COLOR_RGB2GRAY)) ** 2
        ps.append(10 * np.log10(255 ** 2 / max(e.mean(), 1e-9))); cs.append(10 * np.log10(255 ** 2 / max(e[y0:y1, x0:x1].mean(), 1e-9)))
        vm.stdin.write(np.ascontiguousarray(p).tobytes())
    vm.stdin.close(); assert vm.wait() == 0
    v = np.array([f["metrics"]["vmaf"] for f in json.load(open(log))["frames"]])
    os.remove(log)
    return np.array(ps), np.array(cs), v

def arm(ref, work, out_json, name, kbps, kind, *args):
    res = json.load(open(out_json)) if os.path.exists(out_json) else {}
    key = f"{name}/{kbps}"
    if key in res: return
    os.makedirs(work, exist_ok=True)
    W, H = probe(ref)
    size = lambda s: tuple(map(int, s.split("x")))
    src, vf, post, dw, dh, pre_cpu = ref, None, (lambda f: f), W, H, 0.0
    if kind in ("squish", "squishbase", "squishfast"):
        (bw, bh), (dw, dh) = (size(args[0]), size(args[1])) if kind == "squishbase" else ((W, H), size(args[0]))
        sq = Squish(bw, bh, dw, dh)
        vf = (args[2:] or [None])[0] if kind == "squishbase" else (args[1:] or [None])[0]  # optional ROI on top
        src = f"{work}/src_{kind}_{bw}x{bh}_{dw}x{dh}.mkv"
        if not os.path.exists(src + ".done"):
            w = lossless_writer(dw, dh, src); t = 0.0; n = 0
            for f in frames(ref, W, H):
                t0 = time.process_time()
                b = f if (bw, bh) == (W, H) else cv2.resize(f, (bw, bh), interpolation=cv2.INTER_AREA)
                s = sq.fast(b) if kind == "squishfast" else sq.fwd(b); t += time.process_time() - t0; n += 1
                w.stdin.write(s.tobytes())
            w.stdin.close(); assert w.wait() == 0
            json.dump({"cpu_s": t, "frames": n}, open(src + ".done", "w"))
        pre_cpu = json.load(open(src + ".done"))["cpu_s"]
        post = (lambda f: sq.inv(f)) if (bw, bh) == (W, H) else (lambda f: cv2.resize(sq.inv(f), (W, H), interpolation=cv2.INTER_CUBIC))
    elif kind == "scale":
        dw, dh = size(args[0]); vf = f"scale={dw}:{dh}:flags=bicubic"
        post = lambda f: cv2.resize(f, (W, H), interpolation=cv2.INTER_CUBIC)
    elif kind == "roi":
        vf = args[0]
    mkv = f"{work}/{name}_{kbps}.mkv"
    enc_cpu = encode(src, mkv, kbps, vf)
    dec_cpu = bench([FF, "-nostats", "-benchmark", "-i", mkv, "-f", "null", "-"])
    pk = packets(mkv)
    ps, cs, v = score(mkv, os.environ.get("SCORE_REF", ref), W, H, dw, dh, post, f"{work}/{name}_{kbps}.vmaf.json")
    np.savez_compressed(f"{work}/{name}_{kbps}.npz", psnr=ps, c_psnr=cs, vmaf=v, bytes=pk)
    if not os.environ.get("KEEP"): os.remove(mkv)
    n = len(ps); s = slice(60, None)  # first second skipped in the summary (rate control warm-up)
    res = json.load(open(out_json)) if os.path.exists(out_json) else {}
    res[key] = {"kbps_actual": round(float(pk.sum()) * 8 / (n / 60) / 1000, 1), "psnr": round(float(ps[s].mean()), 3),
                "c_psnr": round(float(cs[s].mean()), 3), "vmaf": round(float(v[s].mean()), 2),
                "vmaf_p5": round(float(np.percentile(v[s], 5)), 2), "enc_cpu_s": round(enc_cpu, 2),
                "pre_cpu_s": round(pre_cpu, 2), "dec_cpu_s": round(dec_cpu, 2), "frames": n, "pixels": dw * dh}
    json.dump(res, open(out_json, "w"), indent=1)
    print(key, res[key], flush=True)

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "ref":
        src, out, W, H = sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
        t = (["-ss", sys.argv[6], "-t", sys.argv[7]] if len(sys.argv) > 6 else [])
        subprocess.run([FF, "-v", "error", "-y"] + t + ["-i", src, "-vf", f"fps=60,scale={W}:{H}:flags=area", "-an", "-c:v",
                        "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", out], check=True)
    elif cmd == "motion":
        prev, out = None, []
        for f in frames(sys.argv[2], 240, 135, "scale=240:135:flags=area"):
            g = cv2.cvtColor(f, cv2.COLOR_RGB2GRAY).astype(np.float32)
            out.append(0.0 if prev is None else float(np.abs(g - prev).mean())); prev = g
        np.save(sys.argv[3], np.array(out, np.float32))
    elif cmd == "arm":
        arm(*sys.argv[2:5], sys.argv[5], int(sys.argv[6]), sys.argv[7], *sys.argv[8:])
