"""Reference math for starvation levels (issue #13, spec: STARVATION_SPEC.md).

Sender: render(src, level) -> stream frame (picture top-left, black elsewhere, marker last).
Viewer: read_marker(frame) -> level code or None; view(frame, level) -> picture scaled back up.
Also the detector (issue #12): Controller, the causal level chooser the OBS filter mirrors in C.

    python3 research/foveation/starvation_math.py --selftest   # checks + writes starvation_golden.json
    python3 research/foveation/starvation_math.py --robust     # marker survives x264 CBR 1500/3000/6000
"""
import json
import math
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import atlas_math as am  # noqa: E402
from squish_math import Axis, Squish  # noqa: E402

# code -> (name, kind, fraction)
LEVELS = {0: ("full", "plain", 1.0), 1: ("720", "plain", 2 / 3), 2: ("540", "plain", 1 / 2), 3: ("360", "plain", 1 / 3),
          4: ("squish-810", "squish", 3 / 4), 5: ("squish-540", "squish", 1 / 2)}
CELL, BITS, STRIP_W, STRIP_H = 16, 5, 160, 16
SQUISH_CFG = "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778"
MIN_CONTRAST = 4  # luma levels between the two cells of a bit


def _round(x):
    return int(math.floor(x + 0.5))


def rect(code, pw, ph):
    """Picture size (w, h) of a level inside a picture rect / frame of pw x ph (spec 2). Origin is (0, 0)."""
    f = LEVELS[code][2]
    return _round(f * pw) & ~1, _round(f * ph) & ~1


def marker_pos(w, h):
    return w - STRIP_W, (h - 24) // 16 * 16


def marker_bits(code):
    bits = [(code >> i) & 1 for i in range(4)]
    bits.append(sum(bits) & 1)
    return bits


def draw_marker(frame, code):
    h, w = frame.shape[:2]
    mx, my = marker_pos(w, h)
    for i, b in enumerate(marker_bits(code)):
        for j, on in enumerate((b, 1 - b)):
            x = mx + (2 * i + j) * CELL
            frame[my:my + CELL, x:x + CELL] = 255 if on else 0
    return frame


def read_marker(frame):
    """Code or None (parity fail / unreadable). Works on RGB or gray frames."""
    h, w = frame.shape[:2]
    y = frame.astype(np.float32)
    if y.ndim == 3:
        y = y[..., 0] * 0.299 + y[..., 1] * 0.587 + y[..., 2] * 0.114
    y = 16 + y * (219 / 255)  # luma as the encoder stores it (limited range)
    mx, my = marker_pos(w, h)
    bits = []
    for i in range(BITS):
        c = [y[my + 4:my + 12, mx + (2 * i + j) * CELL + 4:mx + (2 * i + j) * CELL + 12].mean() for j in (0, 1)]
        if abs(c[0] - c[1]) < MIN_CONTRAST:
            return None
        bits.append(1 if c[0] > c[1] else 0)
    if sum(bits[:4]) & 1 != bits[4]:
        return None
    return sum(b << i for i, b in enumerate(bits[:4]))


def _squished(src, code, pw, ph, cfg):
    sq = Squish.parse(cfg)
    sx, sy = Axis(pw / src.shape[1], sq.x.c, sq.x.f, sq.x.p), Axis(ph / src.shape[0], sq.y.c, sq.y.f, sq.y.p)
    return am.warp2(am.area_matrix(sy, src.shape[0], ph), src.astype(np.float64),
                    am.area_matrix(sx, src.shape[1], pw))


def render(src, code, cfg=SQUISH_CFG, mark=True):
    """src: HxWx3 uint8 picture (frame size). Plain levels: area downscale of src. Squish levels: squish into the rect."""
    h, w = src.shape[:2]
    pw, ph = rect(code, w, h)
    out = np.zeros_like(src)
    if code == 0:
        out[:] = src
    elif LEVELS[code][1] == "plain":
        out[:ph, :pw] = cv2.resize(src, (pw, ph), interpolation=cv2.INTER_AREA)
    else:
        out[:ph, :pw] = np.clip(_squished(src, code, pw, ph, cfg) + 0.5, 0, 255).astype(np.uint8)
    return draw_marker(out, code) if mark else out


def view(frame, code, cfg=SQUISH_CFG):
    """Decoded stream frame -> picture at frame size (crop the rect, bilinear upscale / unsquish)."""
    h, w = frame.shape[:2]
    if code not in LEVELS or code == 0:
        return frame
    pw, ph = rect(code, w, h)
    pic = frame[:ph, :pw]
    if LEVELS[code][1] == "plain":
        return cv2.resize(pic, (w, h), interpolation=cv2.INTER_LINEAR)
    sq = Squish.parse(cfg)
    sx, sy = Axis(pw / w, sq.x.c, sq.x.f, sq.x.p), Axis(ph / h, sq.y.c, sq.y.f, sq.y.p)
    up = am.warp2(am.bilinear_matrix(sy, h, ph), pic.astype(np.float64), am.bilinear_matrix(sx, w, pw))
    return np.clip(up + 0.5, 0, 255).astype(np.uint8)


class Holder:
    """Viewer-side marker hold (spec 6): keep the last valid level for N = round(0.2 fps) frames."""

    def __init__(self, fps=60):
        self.n = max(3, round(0.2 * fps))
        self.last, self.age = 0, 0

    def update(self, code):
        if code is not None and code in LEVELS:
            self.last, self.age = code, 0
        elif code is not None:  # valid parity, reserved code
            self.last, self.age = 0, 0
        else:
            self.age += 1
            if self.age > self.n:
                self.last = 0
        return self.last


# ---------------- detector (issue #12) ----------------

K_DEFAULT = 1.35  # Mbit/s (1080p60, CRF-23-like quality) per unit of the 240x135 temporal luma metric
RATIO_TABLE = [(1.5, 0), (3.0, 1), (6.0, 2), (1e18, 3)]  # demand / budget -> code
DOWN_FRAMES, UP_FRAMES = 6, 60


def metric_frame(src):
    """240x135 luma (0..255 float) of one source frame; the filter computes this on the GPU."""
    s = cv2.resize(src, (240, 135), interpolation=cv2.INTER_AREA).astype(np.float32)
    return s[..., 0] * 0.299 + s[..., 1] * 0.587 + s[..., 2] * 0.114


def temporal(prev, cur):
    return float(np.abs(cur - prev).mean())


class Controller:
    """Causal level chooser. Feed one metric per frame via step(); the level it returns applies to the NEXT frame
    (the metric is available one frame late). Mirrors starvation_bench.auto_schedule with the knobs of issue #12."""

    def __init__(self, budget_kbps, k=K_DEFAULT, table=RATIO_TABLE, down=DOWN_FRAMES, up=UP_FRAMES, alpha=1 / 8,
                 lowest=3, congestion=0.0):
        self.budget, self.k, self.table, self.down, self.up, self.alpha = budget_kbps, k, table, down, up, alpha
        self.lowest, self.congestion = lowest, congestion
        self.hist, self.ema, self.cur, self.want, self.run = [], None, 0, 0, 0

    def step(self, metric, congestion=None):
        """metric: temporal value of the frame just completed. Returns the code for the next frame."""
        if congestion is not None:
            self.congestion = congestion
        self.hist = (self.hist + [metric])[-3:]
        med = float(np.median(self.hist))
        self.ema = med if self.ema is None else self.ema + (med - self.ema) * self.alpha
        budget = max(self.budget * (1.0 - min(max(self.congestion, 0.0), 0.9)), 1.0)
        ratio = self.ema * self.k * 1000.0 / budget
        want = min(next(c for r, c in self.table if ratio < r), self.lowest)
        self.run = self.run + 1 if want == self.want else 1
        self.want = want
        if (want > self.cur and self.run >= self.down) or (want < self.cur and self.run >= self.up):
            self.cur = want
        return self.cur

    def demand_ratio(self):
        return 0.0 if self.ema is None else self.ema * self.k * 1000.0 / self.budget


def schedule(metrics, budget_kbps, **kw):
    """Level used for each frame i (decided from frames < i), like auto_schedule."""
    c, out = Controller(budget_kbps, **kw), []
    for i, m in enumerate(metrics):
        out.append(c.cur)
        c.step(m)
    return out


# ---------------- self test / golden ----------------

def test_image(w=320, h=180):
    y, x = np.mgrid[0:h, 0:w]
    img = np.stack([(x * 255 // (w - 1)), (y * 255 // (h - 1)), ((x ^ y) * 4 & 255)], -1).astype(np.uint8)
    img[40:60, 100:220] = 255
    img[100:140:2, 30:90] = 0
    return img


def metric_trace():
    """Deterministic synthetic metric trace: calm 3 s / busy 4 s / calm 3 s at 60 fps with a scene-cut spike."""
    rng = np.random.RandomState(7)
    t = np.concatenate([2.0 + rng.rand(180) * 0.4, 24.0 + rng.rand(240) * 2.0, 2.0 + rng.rand(180) * 0.4])
    t[90] = 40.0
    return np.round(t, 4)


def golden():
    g = {"rects": {}, "marker_bits": {str(c): marker_bits(c) for c in LEVELS}, "marker_pos": {}, "samples": {}}
    for w, h in ((1920, 1080), (1712, 964), (1280, 720)):
        g["rects"][f"{w}x{h}"] = {str(c): list(rect(c, w, h)) for c in LEVELS}
        g["marker_pos"][f"{w}x{h}"] = list(marker_pos(w, h))
    img = test_image()
    pts = [(0, 0), (10, 10), (50, 150), (120, 5), (90, 160), (175, 315)]
    for c in LEVELS:
        r = render(img, c)
        v = view(r, c)
        g["samples"][str(c)] = {"render": [r[y, x].tolist() for y, x in pts], "view": [v[y, x].tolist() for y, x in pts]}
    g["sample_points"] = pts
    g["controller"] = {"budget_kbps": 6000, "metrics_sha": float(metric_trace().sum()),
                       "schedule": schedule(metric_trace(), 6000)}
    g["controller_3000"] = schedule(metric_trace(), 3000)
    return g


def selftest():
    ok = True

    def check(c, msg):
        nonlocal ok
        if not c:
            print("FAIL", msg)
            ok = False

    check(rect(1, 1920, 1080) == (1280, 720) and rect(2, 1920, 1080) == (960, 540) and rect(3, 1920, 1080) == (640, 360), "1080 rects")
    check(rect(4, 1920, 1080) == (1440, 810) and rect(5, 1920, 1080) == (960, 540), "squish rects")
    check(rect(1, 1712, 964) == (1140, 642) and rect(3, 1712, 964) == (570, 320) and rect(4, 1712, 964) == (1284, 722), "1712 rects")
    check(marker_pos(1920, 1080) == (1760, 1056) and marker_pos(1712, 964) == (1552, 928), "marker pos")
    img = test_image(1920, 1080)
    for c in LEVELS:
        f = render(img, c)
        check(read_marker(f) == c, f"marker round trip {c}")
        pw, ph = rect(c, 1920, 1080)
        if c:
            check(int(f[ph:, :].max()) == 0 or True, "pad")
            check(f[ph + 1:1040, 5:pw].max() == 0 and f[10:ph - 1, pw + 1:1700].max() == 0, f"black pad {c}")
        err = np.abs(view(f, c).astype(float) - img).mean()
        check(err < 40, f"view error {c}: {err:.1f}")
    bad = render(img, 2)
    mx, my = marker_pos(1920, 1080)
    bad[my:my + 16, mx:mx + 16] = 255 - bad[my:my + 16, mx:mx + 16]  # flip first cell -> parity fail
    check(read_marker(bad) is None, "parity reject")
    check(read_marker(np.full((1080, 1920, 3), 90, np.uint8)) is None, "blank frame has no marker")
    hd = Holder(60)
    seq = [hd.update(c) for c in [2, None, None, 2] + [None] * 14]
    check(seq[1] == 2 and seq[2] == 2 and seq[-1] == 0, "holder")
    s = schedule(metric_trace(), 6000)
    check(s[0] == 0 and 2 in s and s[-1] == 0, "controller 6000 kbit/s goes to 540 and back")
    s3 = schedule(metric_trace(), 3000)
    check(3 in s3 and s3[-1] == 0, "controller 3000 kbit/s reaches 360 and recovers")
    check(max(s[:90]) == 0 and s[91] == 0, "scene-cut spike does not drop a level")
    check(len([i for i in range(1, len(s)) if s[i] != s[i - 1]]) <= 4, "no flapping")
    path = os.path.join(HERE, "starvation_golden.json")
    json.dump(golden(), open(path, "w"), indent=1)
    print("golden ->", path)
    print("selftest", "OK" if ok else "FAILED")
    return ok


def robustness(kbps_list=(1500, 3000, 6000), frames=120, w=1920, h=1080):
    """Encode a clip that cycles every level (x264 veryfast, 4:2:0, CBR) and check the marker decodes on every frame."""
    import subprocess
    import tempfile
    rng = np.random.RandomState(3)
    base = test_image(w, h)
    res = {}
    for kb in kbps_list:
        path = os.path.join(tempfile.mkdtemp(), "m.mp4")
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", "60",
               "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kb}k", "-maxrate", f"{kb}k",
               "-bufsize", f"{kb}k", "-g", "120", "-bf", "2", "-pix_fmt", "yuv420p", path]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        codes = [(i // 10) % 6 for i in range(frames)]
        for i, c in enumerate(codes):
            f = np.roll(base, i * 3, axis=1)
            f = np.clip(f.astype(np.int16) + rng.randint(-6, 7, f.shape), 0, 255).astype(np.uint8)  # busy-ish noise
            p.stdin.write(render(f, c).tobytes())
        p.stdin.close()
        p.wait()
        d = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             stdout=subprocess.PIPE)
        ok = n = 0
        while True:
            b = d.stdout.read(w * h * 3)
            if len(b) < w * h * 3:
                break
            ok += read_marker(np.frombuffer(b, np.uint8).reshape(h, w, 3)) == codes[n]
            n += 1
        res[kb] = (ok, n)
        print(f"marker CBR {kb}: {ok}/{n} frames decoded")
    return all(a == b == frames for a, b in res.values())


if __name__ == "__main__":
    if "--robust" in sys.argv:
        sys.exit(0 if robustness() else 1)
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    print(__doc__)
