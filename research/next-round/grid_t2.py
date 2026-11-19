"""T2 grid: method settings (centre size, transmitted size) against the downscale at the same size, plus plain, with
region-profile scores. One resumable run; every finished cell is one line in OUT_DIR/results.jsonl.

  python grid_t2.py OUT_DIR REF1.mkv [REF2.mkv ...]

Environment (all optional):
  CODECS   x264,hevc_nvenc                       encoders (stack_bench names)
  RATES    500,1000,1500,3000,6000               kbit/s targets (CBR); measured rate is recorded
  SIZES    1440x810,1280x720,960x540,640x360,284x160    transmitted sizes
  CENTRES  0.05,0.1,0.2,0.3,0.5                  sharp centre, fraction of the frame width and height (0.3 = centre 30%)
  VBOXES   0.3,0.5,1.0                           boxes scored with VMAF
  MODELS   vmaf                                  vmaf or vmaf,neg
  NO_VMAF  1                                     skip VMAF (PSNR rings only)
  MIN_SECS 10                                    shortest clip accepted
  ROI      1                                     add encoder-ROI arms (x264 addroi on a centre box, rate control pushes the rest up)
  ROI_CENTRES 0.1,0.3,0.5   ROI_Q -0.2,-0.4,-0.6   box (fraction of frame) and qoffset (-0.1 is about 5 QP steps) of the plain-ROI arms
  ROI_SQ   sq960x540_c10,...                     method arms that also get ROI on the transmitted centre box (names as in results)
  ROI_SQ_Q -0.2,-0.4                             qoffsets for those method+ROI arms
  ROI_CODECS x264                                codecs that run ROI arms (NVENC ignores addroi, so default x264 only)
  SMOOTH_ROI 1  SROI_CENTRES 0.1,0.3  SROI_Q -0.4,-0.6  SROI_STEPS 3
                                                 smooth-falloff ROI: STEPS nested boxes from the centre box outward, qoffset
                                                 stepping from Q to Q/STEPS (0 outside); inner box listed first, which x264 applies on overlap
  CAP_VBR  1.5                                   capped VBR (x264 only): -b:v = target (average), -maxrate = CAP_VBR x target, bufsize 2x maxrate; run into its own OUT_DIR
  ARMS_ONLY roi                                  run only arms whose name contains this text
  ONLY_ARMS a,b,c                                run only these exact arm names, plus plain and every scale arm (held-out run)
Infeasible settings (squeeze beyond the density floor for any curve) are recorded as infeasible and skipped.
"""
import json, os, subprocess, sys
import numpy as np, cv2
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "stack"))
import stack_bench as sb
from squish_math import Axis
from scipy import sparse

W, H = 1920, 1080
env = lambda k, d: os.environ.get(k, d)
CODECS = env("CODECS", "x264,hevc_nvenc").split(",")
RATES = [int(x) for x in env("RATES", "500,1000,1500,3000,6000").split(",")]
SIZES = [tuple(int(v) for v in s.split("x")) for s in env("SIZES", "1440x810,1280x720,960x540,640x360,284x160").split(",")]
CENTRES = [float(x) for x in env("CENTRES", "0.05,0.1,0.2,0.3,0.5").split(",")]
VBOXES = [float(x) for x in env("VBOXES", "0.3,0.5,1.0").split(",")]
MODELS = env("MODELS", "vmaf").split(",")
ROI = env("ROI", "") != ""
ROI_C = [float(x) for x in env("ROI_CENTRES", "0.1,0.3,0.5").split(",")]
ROI_Q = [float(x) for x in env("ROI_Q", "-0.2,-0.4,-0.6").split(",")]
ROI_SQ = [x for x in env("ROI_SQ", "").split(",") if x]
ROI_SQ_Q = [float(x) for x in env("ROI_SQ_Q", "-0.2,-0.4").split(",")]
ROI_CODECS = env("ROI_CODECS", "x264").split(",")
ARMS_ONLY = env("ARMS_ONLY", "")
SROI = env("SMOOTH_ROI", "") != ""
SROI_C = [float(x) for x in env("SROI_CENTRES", "0.1,0.3").split(",")]
SROI_Q = [float(x) for x in env("SROI_Q", "-0.4,-0.6").split(",")]
SROI_N = int(env("SROI_STEPS", "3"))
ONLY_ARMS = {x for x in env("ONLY_ARMS", "").split(",") if x}
CAP_VBR = float(env("CAP_VBR", "0"))
FPS = int(env("FPS", "60"))  # source and scoring frame rate; KEYINT_S is seconds, so at 120 use KEYINT_S=4 for a 2 s GOP
BOXES = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]
def addroi(x, y, w, h, q): return f"addroi=x={x}:y={y}:w={w}:h={h}:qoffset={q}"
SKIP = FPS  # first second not scored

def encode_capped(src, out, kbps, vf=None):
    """Same as stack_bench.encode for x264, but average = kbps and peaks up to CAP_VBR x kbps (capped VBR, not CBR)."""
    gop = int(round(float(env("KEYINT_S", "2")) * 60)); cap = int(kbps * CAP_VBR)
    codec = ["-c:v", "libx264", "-preset", "veryfast", "-keyint_min", str(gop), "-sc_threshold", "0"]
    rate = ["-b:v", f"{kbps}k", "-maxrate", f"{cap}k", "-bufsize", f"{2 * cap}k", "-g", str(gop), "-pix_fmt", "yuv420p"]
    cpu = sb.bench([sb.FF, "-nostats", "-benchmark", "-y", "-i", src] + (["-vf", vf] if vf else []) + codec + rate + [out])
    return cpu - sb.bench([sb.FF, "-nostats", "-benchmark", "-i", src, "-f", "null", "-"])

class SquishP(sb.Squish):
    """Squish with the sharp-centre size c (half-width fraction) and curve p as parameters."""
    def __init__(s, sw, sh, c, p):
        s.W, s.H, s.sw, s.sh, s.c, s.p = W, H, sw, sh, c, p
        (s.AX, ux), (s.AY, uy) = s.axisp(W, sw), s.axisp(H, sh)
        s.MX, s.MY = np.tile(ux[None, :], (H, 1)), np.tile(uy[:, None], (1, W))
    def axisp(s, n, m):
        ax = Axis(r=m / n, c=s.c, f=0.5, p=s.p)
        ue = ax.forward(np.linspace(0, 1, n + 1)) * m
        j = np.arange(m)
        ov = np.clip(np.minimum((j + 1)[:, None], ue[None, 1:]) - np.maximum(j[:, None], ue[None, :-1]), 0, None)
        return sparse.csr_matrix((ov / ov.sum(1, keepdims=True)).astype(np.float32)), \
               (ax.forward((np.arange(n) + 0.5) / n) * m - 0.5).astype(np.float32)

def pick_p(c, r):
    for p in (1.0, 0.5, 0.25, 0.1, 0.05, 0.02):
        if Axis(r=r, c=c, f=0.5, p=p).valid(): return p
    return None

def decode(path, w, h):
    d = subprocess.Popen([sb.FF, "-v", "error", "-i", path, "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE)
    n = w * h * 3
    while True:
        b = d.stdout.read(n)
        if len(b) < n: break
        yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
    d.wait()

def box_geom(b):
    cw, ch = int(W * b) // 2 * 2, int(H * b) // 2 * 2
    return cw, ch, (W - cw) // 2, (H - ch) // 2

def vmaf_procs(ref, work, tag):
    procs = {}
    for b in VBOXES:
        cw, ch, x, y = box_geom(b)
        for m in MODELS:
            log = f"_v_{tag}_{b}_{m}.json"; crop = f"crop={cw}:{ch}:{x}:{y}"
            model = "model=version=vmaf_v0.6.1neg:" if m == "neg" else ""
            g = f"[0:v]format=yuv420p,{crop}[d];[1:v]format=yuv420p,{crop}[r];[d][r]libvmaf={model}log_fmt=json:log_path={log}:n_threads=2"
            procs[(b, m)] = (subprocess.Popen([sb.VFF, "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                             "-i", "-", "-r", str(FPS), "-i", os.path.abspath(ref), "-lavfi", g, "-f", "null", "-"], stdin=subprocess.PIPE, cwd=work),
                             os.path.join(work, log))
    return procs

def cell(ref, work, codec, kbps, src, vf, post, tx, tag):
    os.environ["CODEC"] = codec
    tmp = os.path.join(work, "_tx.mkv")
    (encode_capped if CAP_VBR else sb.encode)(src, tmp, kbps, vf)
    by = sb.packets(tmp)[SKIP:]
    rate = float(by.sum()) * 8 / (len(by) / FPS) / 1000
    procs = {} if env("NO_VMAF", "") else vmaf_procs(ref, work, tag)
    rings = []
    for d, g in zip(decode(tmp, *tx), decode(ref, W, H)):
        p = post(d)
        e = (cv2.cvtColor(p, cv2.COLOR_RGB2GRAY).astype(np.float32) - cv2.cvtColor(g, cv2.COLOR_RGB2GRAY)) ** 2
        sse = []
        for b in BOXES:
            cw, ch, x, y = box_geom(b); sse.append((float(e[y:y + ch, x:x + cw].sum()), cw * ch))
        rings.append(sse)
        for pr, _ in procs.values(): pr.stdin.write(np.ascontiguousarray(p).tobytes())
    out = {"kbps": round(rate, 1), "ring_psnr": {}, "vmaf": {}}
    R = np.array(rings)[SKIP:]  # frames, boxes, (sse, n)
    for i, b in enumerate(BOXES):
        s, n = R[:, i, 0], R[:, i, 1]
        if i: s, n = s - R[:, i - 1, 0], n - R[:, i - 1, 1]
        out["ring_psnr"][str(b)] = round(float(np.mean(10 * np.log10(255 ** 2 / np.maximum(s / n, 1e-9)))), 3)
    for (b, m), (pr, log) in procs.items():
        pr.stdin.close(); assert pr.wait() == 0
        v = np.array([f["metrics"]["vmaf"] for f in json.load(open(log))["frames"]])[SKIP:]; os.remove(log)
        out["vmaf"][f"{b}_{m}"] = round(float(v.mean()), 2); out["vmaf"][f"{b}_{m}_p5"] = round(float(np.percentile(v, 5)), 2)
    os.remove(tmp)
    return out

def main():
    work, refs = sys.argv[1], sys.argv[2:]
    os.makedirs(work, exist_ok=True)
    res = os.path.join(work, "results.jsonl")
    done = set()
    if os.path.exists(res):
        for l in open(res):
            r = json.loads(l); done.add((r["clip"], r["codec"], r["kbps_target"], r["arm"]))
    def write(rec):
        open(res, "a").write(json.dumps(rec) + "\n"); print(rec["clip"][-14:], rec["codec"], rec["kbps_target"], rec["arm"], rec.get("kbps"), rec.get("vmaf", {}).get("0.5_vmaf"), flush=True)
    for ref in refs:
        clip = os.path.basename(os.path.dirname(os.path.abspath(ref))) if os.path.basename(ref).lower() == "ref.mkv" else os.path.splitext(os.path.basename(ref))[0]  # ref.mkv clips are named by their folder
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", ref], capture_output=True, text=True).stdout)
        if dur < float(env("MIN_SECS", "10")): sys.exit(f"REFUSED: {clip} is {dur:.1f} s, under MIN_SECS")
        # arms: (name, tx size, source file or None, vf, post, extra)
        arms = [("plain", (W, H), ref, None, lambda f: f, {})]
        for sw, sh in SIZES:
            arms.append((f"scale{sw}x{sh}", (sw, sh), ref, f"scale={sw}:{sh}:flags=bicubic",
                         lambda f: cv2.resize(f, (W, H), interpolation=cv2.INTER_CUBIC), {}))
        for sw, sh in SIZES:
            for ctr in CENTRES:
                p = pick_p(ctr / 2, sw / W)
                name = f"sq{sw}x{sh}_c{int(round(ctr * 100))}"
                if p is None:
                    for codec, kbps in [(c, k) for c in CODECS for k in RATES]:
                        if (clip, codec, kbps, name) not in done:
                            write({"clip": clip, "codec": codec, "kbps_target": kbps, "arm": name, "infeasible": True}); done.add((clip, codec, kbps, name))
                    continue
                arms.append((name, (sw, sh), None, None, None, {"c": ctr / 2, "p": p}))
                if ROI and name in ROI_SQ:  # method + ROI on the sharp centre box of the transmitted frame
                    bw, bh = int(W * ctr) // 2 * 2, int(H * ctr) // 2 * 2
                    for q in ROI_SQ_Q:
                        arms.append((f"{name}_roi{int(round(-q * 100))}", (sw, sh), None, addroi((sw - bw) // 2, (sh - bh) // 2, bw, bh, q), None,
                                     {"c": ctr / 2, "p": p, "roi_q": q}))
        if ROI:  # plain-resolution ROI arms: the centre box gets a negative QP offset, the rate control raises the rest
            for ctr in ROI_C:
                bw, bh = int(W * ctr) // 2 * 2, int(H * ctr) // 2 * 2
                for q in ROI_Q:
                    arms.append((f"roi_c{int(round(ctr * 100))}_q{int(round(-q * 100))}", (W, H), ref, addroi((W - bw) // 2, (H - bh) // 2, bw, bh, q), lambda f: f, {}))
        if SROI:  # smooth ROI: nested boxes, inner first (libx264 uses the first listed region where regions overlap)
            for ctr in SROI_C:
                for q in SROI_Q:
                    vf = []
                    for i in range(SROI_N):
                        f = ctr + (1 - ctr) * i / SROI_N
                        bw, bh = int(W * f) // 2 * 2, int(H * f) // 2 * 2
                        vf.append(addroi((W - bw) // 2, (H - bh) // 2, bw, bh, round(q * (1 - i / SROI_N), 4)))
                    arms.append((f"sroi_c{int(round(ctr * 100))}_q{int(round(-q * 100))}", (W, H), ref, ",".join(vf), lambda f: f, {}))
        if ARMS_ONLY: arms = [a for a in arms if ARMS_ONLY in a[0]]
        if ONLY_ARMS: arms = [a for a in arms if a[0] in ONLY_ARMS or a[0] == "plain" or a[0].startswith("scale")]
        for name, tx, src, vf, post, extra in arms:
            todo = [(c, k) for c in CODECS for k in RATES if (clip, c, k, name) not in done and (c in ROI_CODECS or "roi" not in name)]
            if not todo: continue
            if extra:
                sq = SquishP(*tx, extra["c"], extra["p"]); post = sq.inv
                src = os.path.join(work, "_sq_src.mkv"); w = sb.lossless_writer(*tx, src, FPS)
                for f in decode(ref, W, H): w.stdin.write(np.ascontiguousarray(sq.fwd(f)).tobytes())
                w.stdin.close(); assert w.wait() == 0
            for codec, kbps in todo:
                rec = cell(ref, work, codec, kbps, src, vf, post, tx, f"{name}_{codec}_{kbps}")
                write({"clip": clip, "codec": codec, "kbps_target": kbps, "arm": name, "tx": f"{tx[0]}x{tx[1]}", **extra, **rec}); done.add((clip, codec, kbps, name))
            if extra: os.remove(src)

if __name__ == "__main__": main()
