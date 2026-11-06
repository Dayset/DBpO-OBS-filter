"""Bitrate starvation: shrink the picture inside a fixed 1080p frame instead of restarting the stream.

When the stream is starved (busy scene, or the uplink drops), the picture is downscaled and placed in
the top-left of the 1920x1080 frame; the rest of the frame is padding. The viewer reads a small code
(the "marker", bottom-right 160x16 px, MB-aligned) that says which scale the frame uses, crops the
picture and scales it back up. The stream never stops and the frame size never changes, so OBS,
Twitch and the player keep running.

    python3 starvation_bench.py build SRC.mp4 WORK           # reference timeline (calm / spin / calm)
    python3 starvation_bench.py load TIMELINE.mkv WORK       # same timeline from the ready-made lossless file
    python3 starvation_bench.py run WORK KBPS NAME [NAME..]  # encode + measure variants
    python3 starvation_bench.py dyn WORK KBPS SCHEDULE [kf]  # e.g. calm1=1080,spin=540,calm2=1080
    python3 starvation_bench.py detect WORK                  # cheap complexity metric vs CRF-23 bits

Variant names: base | true<h> | <h>_<pad>[_c][_nomark] | sq964 | sq<h>
  <h>   picture height 720 / 540 / 360 (16:9 width)
  pad   black | edge (repeat the outer row/column) | mirror
  _c    picture centred instead of top-left; _nomark: no marker
  true<h>: a real <h>p stream (what restarting the stream at a lower resolution would give)
  sq964: Foveated Squish as shipped (1712x964 frame); sq<h>: squished picture of height <h>, edge pad
Viewer: crop + bilinear upscale (or bilinear unsquish), like the extension. Metrics vs the 1080p
reference, luma, per timeline segment, first SETTLE frames of the clip skipped.
"""
import json
import os
import subprocess
import sys

import cv2
import numpy as np
from scipy import sparse
from scipy.ndimage import uniform_filter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atlas_math as am  # noqa: E402
from squish_math import Squish  # noqa: E402
import starvation_math as sm  # noqa: E402

W, H, FPS = 1920, 1080, 60
SEGS = [("calm1", 0, 180), ("spin", 180, 420), ("calm2", 420, 600)]
N = 600
SETTLE = 30
SQ = "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778"
SIZES = {1080: (1920, 1080), 810: (1440, 810), 720: (1280, 720), 540: (960, 540), 360: (640, 360)}
CODES = {1080: 0, 720: 1, 540: 2, 360: 3, 810: 4}
CENTER = (slice(int(H * .35), int(H * .65)), slice(int(W * .35), int(W * .65)))


def ref_frames(work):
    return np.memmap(os.path.join(work, "ref.u8"), np.uint8, "r", shape=(N, H, W, 3))


# ---------------- reference timeline ----------------

def build(src, work):
    os.makedirs(work, exist_ok=True)
    out = np.memmap(os.path.join(work, "ref.u8"), np.uint8, "w+", shape=(N, H, W, 3))

    def still(t):
        b = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", src, "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        return np.frombuffer(b, np.uint8).reshape(1440, 2560, 3)

    def pan(img, n, step):  # slow camera pan: 2240x1260 window moving `step` px/frame (1440p px)
        for i in range(n):
            x = 20 + step * i
            m = np.float32([[2240 / W, 0, 0], [0, 1260 / H, 0]])
            m[0, 2] = x
            yield cv2.warpAffine(img, m, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)

    k = 0
    for f in pan(still(1.0), 180, 1.5):
        out[k] = f
        k += 1
    p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-ss", "5", "-i", src, "-t", "4.2", "-vf",
                          "fps=60,scale=1920:1080:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE)
    while k < 420:
        out[k] = np.frombuffer(p.stdout.read(W * H * 3), np.uint8).reshape(H, W, 3)
        k += 1
    p.kill()
    for f in pan(still(13.0), 180, 1.5):
        out[k] = f
        k += 1
    out.flush()


def load(path, work):
    """Fill WORK/ref.u8 from the published lossless timeline (starvation_timeline_1080p60_rgb_lossless.mkv,
    600 frames, bit-exact with `build`). Frame count and size are checked."""
    os.makedirs(work, exist_ok=True)
    out = np.memmap(os.path.join(work, "ref.u8"), np.uint8, "w+", shape=(N, H, W, 3))
    p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE)
    k = 0
    while True:
        b = p.stdout.read(W * H * 3)
        if len(b) < W * H * 3:
            break
        if k < N:
            out[k] = np.frombuffer(b, np.uint8).reshape(H, W, 3)
        k += 1
    p.wait()
    out.flush()
    if k != N:
        sys.exit(f"expected {N} frames of {W}x{H}, got {k}")


# ---------------- sender ----------------

marker = sm.draw_marker  # reference implementation (STARVATION_SPEC.md)
read_marker = sm.read_marker


class SquishMats:
    def __init__(self, pw, ph):
        sq = Squish.parse(SQ)
        self.down_r = am.area_matrix(sq.y, H, ph).astype(np.float32)
        self.down_c = am.area_matrix(sq.x, W, pw).astype(np.float32).T.copy()
        self.up_r = am.bilinear_matrix(sq.y, H, ph).astype(np.float32)
        self.up_c = am.bilinear_matrix(sq.x, W, pw).astype(np.float32).T.copy()

    @staticmethod
    def _apply(rows, img, cols_t):
        """rows (out_h x h) @ img @ cols_t (w x out_w); both resampling matrices are sparse."""
        rows, cols_t = sparse.csr_matrix(rows), sparse.csr_matrix(cols_t)
        h, w, c = img.shape
        t = (rows @ img.reshape(h, w * c)).reshape(rows.shape[0], w, c)
        t = t.transpose(1, 0, 2).reshape(w, -1)  # (w, out_h*c)
        t = (cols_t.T @ t).reshape(cols_t.shape[1], rows.shape[0], c).transpose(1, 0, 2)
        return np.clip(t + 0.5, 0, 255).astype(np.uint8)

    def squish(self, f):
        return self._apply(self.down_r, f.astype(np.float32), self.down_c)

    def unsquish(self, p):
        return self._apply(self.up_r, p.astype(np.float32), self.up_c)


_SQ = {}


def sqm(h):
    if h not in _SQ:
        _SQ[h] = SquishMats(*SIZES[h])
    return _SQ[h]


def parse(name):
    """-> dict(kind, h, pad, centre, mark)"""
    if name == "base":
        return dict(kind="frame", h=1080, pad=None, centre=False, mark=False)
    if name.startswith("true"):
        return dict(kind="true", h=int(name[4:]), pad=None, centre=False, mark=False)
    if name == "sq964":
        return dict(kind="sq964", h=964, pad=None, centre=False, mark=False)
    if name.startswith("sq"):
        return dict(kind="frame", h=int(name[2:]), pad="edge", centre=False, mark=True, squish=True)
    parts = name.split("_")
    return dict(kind="frame", h=int(parts[0]), pad=parts[1], centre="c" in parts[2:],
                mark="nomark" not in parts[2:])


def place(h, centre):
    pw, ph = SIZES[h]
    return ((W - pw) // 2 // 16 * 16, (H - ph) // 2 // 16 * 16) if centre else (0, 0)


def sender(f, v, h=None):
    h = h or v["h"]
    if v["kind"] == "true":
        return cv2.resize(f, SIZES[h], interpolation=cv2.INTER_AREA)
    if v["kind"] == "sq964":
        return SquishMats._apply(sqm964().down_r, f.astype(np.float32), sqm964().down_c)
    if h == 1080:
        out = f.copy()
    else:
        pw, ph = SIZES[h]
        pic = sqm(h).squish(f) if v.get("squish") else cv2.resize(f, (pw, ph), interpolation=cv2.INTER_AREA)
        x0, y0 = place(h, v["centre"])
        border = {"black": cv2.BORDER_CONSTANT, "edge": cv2.BORDER_REPLICATE, "mirror": cv2.BORDER_REFLECT_101}
        out = cv2.copyMakeBorder(pic, y0, H - ph - y0, x0, W - pw - x0, border[v["pad"] or "edge"], value=0)
    return marker(out, CODES[h]) if v["mark"] else out


_S964 = []


def sqm964():
    if not _S964:
        s = SquishMats.__new__(SquishMats)
        sq = Squish.parse(SQ)
        s.down_r = am.area_matrix(sq.y, H, 964).astype(np.float32)
        s.down_c = am.area_matrix(sq.x, W, 1712).astype(np.float32).T.copy()
        s.up_r = am.bilinear_matrix(sq.y, H, 964).astype(np.float32)
        s.up_c = am.bilinear_matrix(sq.x, W, 1712).astype(np.float32).T.copy()
        _S964.append(s)
    return _S964[0]


def viewer(g, v, h):
    """Decoded stream frame -> what the viewer sees at 1920x1080."""
    if v["kind"] == "true":
        return cv2.resize(g, (W, H), interpolation=cv2.INTER_LINEAR)
    if v["kind"] == "sq964":
        return SquishMats._apply(sqm964().up_r, g.astype(np.float32), sqm964().up_c)
    if h == 1080:
        return g
    pw, ph = SIZES[h]
    x0, y0 = place(h, v["centre"])
    pic = g[y0:y0 + ph, x0:x0 + pw]
    return sqm(h).unsquish(pic) if v.get("squish") else cv2.resize(pic, (W, H), interpolation=cv2.INTER_LINEAR)


# ---------------- encode / decode / metrics ----------------

def stream_size(v):
    if v["kind"] == "true":
        return SIZES[v["h"]]
    if v["kind"] == "sq964":
        return 1712, 964
    return W, H


def encode(gen, w, h, kbps, out, kf_frames=()):
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k",
           "-maxrate", f"{kbps}k", "-bufsize", f"{kbps}k", "-g", "120", "-bf", "2", "-pix_fmt", "yuv420p"]
    if kf_frames:
        cmd += ["-force_key_frames", ",".join(f"{k / FPS:.4f}" for k in kf_frames)]
    p = subprocess.Popen(cmd + [out], stdin=subprocess.PIPE)
    for f in gen:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close()
    p.wait()


def decode(path, w, h):
    p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE)
    while True:
        b = p.stdout.read(w * h * 3)
        if len(b) < w * h * 3:
            break
        yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
    p.wait()


def frame_bits(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "packet=pts,size",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout.split()
    pk = sorted((int(a), int(b)) for a, b in (ln.split(",")[:2] for ln in out))
    return np.array([s * 8 for _, s in pk], np.float64)


def luma(f):
    return f[..., 0] * np.float32(0.299) + f[..., 1] * np.float32(0.587) + f[..., 2] * np.float32(0.114)


def ssim_map(a, b):
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    ma, mb = uniform_filter(a, 7), uniform_filter(b, 7)
    va, vb = uniform_filter(a * a, 7) - ma * ma, uniform_filter(b * b, 7) - mb * mb
    cab = uniform_filter(a * b, 7) - ma * mb
    return ((2 * ma * mb + c1) * (2 * cab + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))


def psnr(a, b):
    mse = float(((a - b) ** 2).mean())
    return 99.0 if mse < 1e-6 else 10 * np.log10(255 ** 2 / mse)


def measure(ref, path, v, sched, kbps_tag):
    w, h = stream_size(v)
    rows = []
    for i, g in enumerate(decode(path, w, h)):
        hh = sched[i]
        got = read_marker(g) if v["mark"] else None
        view = viewer(g, v, hh)
        a, b = luma(ref[i].astype(np.float32)), luma(view.astype(np.float32))
        sm = ssim_map(a, b)
        rows.append((psnr(a, b), psnr(a[CENTER], b[CENTER]), float(sm.mean()), float(sm[CENTER].mean()),
                     -1 if not v["mark"] else int(got == CODES[hh])))
    rows = np.array(rows)
    bits = frame_bits(path)
    res = {"per_frame": rows.tolist(), "bits": bits.tolist()}
    for seg, a, b in SEGS + [("all", 0, N)]:
        a = max(a, SETTLE)
        r = rows[a:b]
        res[seg] = {"psnr": round(r[:, 0].mean(), 2), "psnr_c": round(r[:, 1].mean(), 2),
                    "ssim": round(r[:, 2].mean(), 4), "ssim_c": round(r[:, 3].mean(), 4),
                    "mbit_s": round(bits[a:b].sum() / ((b - a) / FPS) / 1e6, 2)}
        if v["mark"]:
            res[seg]["marker_ok"] = round(float(r[:, 4].mean()), 4)
    return res


def complexity(work):
    """Per-frame temporal metric: mean |luma change| at 240x135 (what a filter can afford every frame)."""
    ref, out, prev = ref_frames(work), [0.0], None
    for i in range(N):
        s = luma(cv2.resize(ref[i], (240, 135), interpolation=cv2.INTER_AREA).astype(np.float32))
        if prev is not None:
            out.append(float(np.abs(s - prev).mean()))
        prev = s
    return np.array(out[:N])


# Calibrated on this clip only (detect: CRF-23 Mbit/s at 1080p60 ~= 1.35 x temporal metric, calm and spin).
MBPS_PER_UNIT = 1.35
LEVELS = [(1.5, 1080), (3.0, 720), (6.0, 540), (1e9, 360)]  # demand / budget -> picture height
DOWN, UP = 6, 60  # frames a lower / higher level must be wanted in a row before switching


def auto_schedule(work, kbps):
    """Causal controller, 1 frame late: median-of-3 metric (a scene cut is a 1-frame spike) -> EMA
    (~0.13 s) -> estimated demand / budget -> level. Drops resolution fast (0.1 s), raises it slowly
    (1 s), so a busy moment is caught at once and calm-busy-calm does not flap."""
    m = complexity(work)
    ema, cur, run_lvl, run, sched = m[1], 1080, 1080, 0, []
    for i in range(N):
        sched.append(cur)
        med = float(np.median(m[max(i - 2, 1):i + 1])) if i else m[1]
        ema += (med - ema) / 8
        want = next(h for r, h in LEVELS if ema * MBPS_PER_UNIT * 1000 / kbps < r)
        run = run + 1 if want == run_lvl else 1
        run_lvl = want
        if want < cur and run >= DOWN or want > cur and run >= UP:
            cur = want
    return sched


def run_variant(work, kbps, name, sched=None, kf=(), pad="black"):
    ref = ref_frames(work)
    v = parse(name.split("@")[0]) if not name.startswith("dyn") else dict(kind="frame", h=0, pad=pad,
                                                                           centre=False, mark=True)
    sched = sched or [v["h"]] * N
    path = os.path.join(work, f"{name}_{kbps}.mp4")
    w, h = stream_size(v)
    if not os.path.exists(path + ".ok"):
        encode((sender(ref[i], v, sched[i]) for i in range(N)), w, h, kbps, path, kf)
        open(path + ".ok", "w").close()
    return measure(ref, path, v, sched, kbps)


def save(work, key, res):
    p = os.path.join(work, "results.json")
    allr = json.load(open(p)) if os.path.exists(p) else {}
    allr[key] = res
    json.dump(allr, open(p, "w"))
    print(key, {k: res[k] for k in ("calm1", "spin", "calm2", "all")}, flush=True)


def detect(work):
    """Cheap complexity metric a filter can compute (1/8-res luma) vs the bits x264 needs at CRF 23."""
    ref = ref_frames(work)
    path = os.path.join(work, "crf23.mp4")
    if not os.path.exists(path + ".ok"):
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
               "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-g", "120",
               "-bf", "2", "-pix_fmt", "yuv420p", path]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for i in range(N):
            p.stdin.write(np.ascontiguousarray(ref[i]).tobytes())
        p.stdin.close()
        p.wait()
        open(path + ".ok", "w").close()
    bits = frame_bits(path)
    tmp, spa, prev = [], [], None
    for i in range(N):
        s = luma(cv2.resize(ref[i], (240, 135), interpolation=cv2.INTER_AREA).astype(np.float32))
        spa.append(float(np.abs(np.diff(s, axis=0)).mean() + np.abs(np.diff(s, axis=1)).mean()))
        tmp.append(0.0 if prev is None else float(np.abs(s - prev).mean()))
        prev = s
    tmp, spa = np.array(tmp), np.array(spa)
    win = 30
    bw = bits[:N // win * win].reshape(-1, win).sum(1) * FPS / win / 1e6
    tw = tmp[:N // win * win].reshape(-1, win).mean(1)
    sw = spa[:N // win * win].reshape(-1, win).mean(1)
    res = {"window_s": win / FPS, "crf23_mbit_s": bw.round(1).tolist(), "temporal": tw.round(2).tolist(),
           "spatial": sw.round(2).tolist(), "corr_temporal": round(float(np.corrcoef(tw, bw)[0, 1]), 3),
           "corr_spatial": round(float(np.corrcoef(sw, bw)[0, 1]), 3),
           "corr_product": round(float(np.corrcoef(tw * sw, bw)[0, 1]), 3)}
    for seg, a, b in SEGS:
        res[seg] = {"crf23_mbit_s": round(bits[a:b].sum() / ((b - a) / FPS) / 1e6, 1),
                    "temporal": round(float(tmp[max(a, 1):b].mean()), 2), "spatial": round(float(spa[a:b].mean()), 2)}
    save_plain(work, "detect", res)


def save_plain(work, key, res):
    p = os.path.join(work, "results.json")
    allr = json.load(open(p)) if os.path.exists(p) else {}
    allr[key] = res
    json.dump(allr, open(p, "w"))
    print(key, json.dumps(res), flush=True)


def main():
    cmd = sys.argv[1]
    if cmd == "build":
        build(sys.argv[2], sys.argv[3])
    elif cmd == "load":
        load(sys.argv[2], sys.argv[3])
    elif cmd == "run":
        work, kbps = sys.argv[2], int(sys.argv[3])
        done = json.load(open(os.path.join(work, "results.json"))) if os.path.exists(
            os.path.join(work, "results.json")) else {}
        for name in sys.argv[4:]:
            if f"{name}@{kbps}" not in done:
                save(work, f"{name}@{kbps}", run_variant(work, kbps, name))
    elif cmd == "dyn":
        work, kbps = sys.argv[2], int(sys.argv[3])
        opts = sys.argv[5:]
        kf, pad = "kf" in opts, next((o[4:] for o in opts if o.startswith("pad=")), "black")
        if sys.argv[4] == "auto":
            sched, tag = auto_schedule(work, kbps), "auto"
        else:
            sch = dict(x.split("=") for x in sys.argv[4].split(","))
            sched = []
            for seg, a, b in SEGS:
                sched += [int(sch[seg])] * (b - a)
            tag = "-".join(sch[s] for s, _, _ in SEGS)
        switches = [i for i in range(1, N) if sched[i] != sched[i - 1]]
        name = f"dyn_{tag}_{pad}" + ("_kf" if kf else "")
        res = run_variant(work, kbps, name, sched, switches if kf else (), pad)
        res["switches"] = switches
        res["levels"] = [sched[s] for s in [0] + switches]
        b = np.array(res["bits"])
        res["switch_cost"] = [{"frame": s, "kbit_10f_after": round(b[s:s + 10].sum() / 1e3, 1),
                               "kbit_10f_before": round(b[s - 10:s].sum() / 1e3, 1),
                               "psnr_c_10f_after": round(float(np.mean([r[1] for r in res["per_frame"][s:s + 10]])), 2),
                               "psnr_c_10f_before": round(float(np.mean([r[1] for r in res["per_frame"][s - 10:s]])), 2)}
                              for s in switches]
        save(work, f"{name}@{kbps}", res)
        print(json.dumps({"switch_cost": res["switch_cost"], "levels": res["levels"], "switches": switches}))
    elif cmd == "detect":
        detect(sys.argv[2])


if __name__ == "__main__":
    main()
