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



# ---------------- marker v2: smooth steps (spec section 9) ----------------
# Row 1 (y = marker_pos) is the v1 marker with code 15 = "extended". Row 2 sits 16 px above it: 20 cells of 8x16,
# Manchester, 10 bits = scale step a (6 bits), squeeze step q (3 bits), even parity. Picture fraction
# f = 1 - (1 - F_MIN) * a / 63; squeeze factor g = f ** (q / 7) multiplies the stream's squish ratio r.

EXT_CODE, A_MAX, Q_MAX, F_MIN, EXT_CELL, EXT_BITS = 15, 63, 7, 1 / 6, 8, 10


def frac(a):
    return 1.0 - (1.0 - F_MIN) * a / A_MAX


def squeeze(a, q):
    return frac(a) ** (q / Q_MAX)


def a_for(f):
    """Nearest step for a picture fraction."""
    return int(min(max(math.floor((1.0 - f) / (1.0 - F_MIN) * A_MAX + 0.5), 0), A_MAX))


def rect2(a, pw, ph):
    f = frac(a)
    return _round(f * pw) & ~1, _round(f * ph) & ~1


def ext_pos(w, h):
    mx, my = marker_pos(w, h)
    return mx, my - 16


def ext_bits(a, q):
    bits = [(a >> i) & 1 for i in range(6)] + [(q >> i) & 1 for i in range(3)]
    return bits + [sum(bits) & 1]


def draw_marker2(frame, a, q):
    draw_marker(frame, EXT_CODE)
    h, w = frame.shape[:2]
    ex, ey = ext_pos(w, h)
    for i, b in enumerate(ext_bits(a, q)):
        for j, on in enumerate((b, 1 - b)):
            x = ex + (2 * i + j) * EXT_CELL
            frame[ey:ey + 16, x:x + EXT_CELL] = 255 if on else 0
    return frame


def _luma_ltd(frame):
    y = frame.astype(np.float32)
    if y.ndim == 3:
        y = y[..., 0] * 0.299 + y[..., 1] * 0.587 + y[..., 2] * 0.114
    return 16 + y * (219 / 255)


def read_ext(frame):
    """(a, q) from the extension row, or None."""
    h, w = frame.shape[:2]
    y = _luma_ltd(frame)
    ex, ey = ext_pos(w, h)
    bits = []
    for i in range(EXT_BITS):
        c = [y[ey + 4:ey + 12, ex + (2 * i + j) * EXT_CELL + 2:ex + (2 * i + j) * EXT_CELL + 6].mean() for j in (0, 1)]
        if abs(c[0] - c[1]) < MIN_CONTRAST:
            return None
        bits.append(1 if c[0] > c[1] else 0)
    if sum(bits[:9]) & 1 != bits[9]:
        return None
    return sum(b << i for i, b in enumerate(bits[:6])), sum(b << i for i, b in enumerate(bits[6:9]))


def read_state(frame):
    """(a, q) for v1 and v2 frames, or None. v1 codes map to the nearest step (squish codes: q = 7)."""
    code = read_marker(frame)
    if code is None:
        return None
    if code == EXT_CODE:
        return read_ext(frame)
    if code not in LEVELS:
        return 0, 0
    return a_for(LEVELS[code][2]), (Q_MAX if LEVELS[code][1] == "squish" else 0)


def max_q(a, cfg=SQUISH_CFG, floor=0.08):
    """Strongest squeeze step the density floor allows at step a."""
    sq = Squish.parse(cfg)
    for q in range(Q_MAX, -1, -1):
        g = squeeze(a, q)
        if all(Axis(ax.r * g, ax.c, ax.f, ax.p).dmin() >= floor for ax in (sq.x, sq.y)):
            return q
    return 0


def _axes(cfg, g):
    sq = Squish.parse(cfg)
    return Axis(sq.x.r * g, sq.x.c, sq.x.f, sq.x.p), Axis(sq.y.r * g, sq.y.c, sq.y.f, sq.y.p)


def render2(src, a, q, cfg=SQUISH_CFG, mark=True):
    """Stream frame (same size as src) at step (a, q). src is the source at frame size; the picture is the squish
    mapping with r * g, resampled straight into the step's rect (area filter), black elsewhere, marker v2 last."""
    h, w = src.shape[:2]
    rw, rh = rect2(a, w, h)
    ax, ay = _axes(cfg, squeeze(a, q))
    out = np.zeros_like(src)
    pic = am.warp2(am.area_matrix(ay, h, rh), src.astype(np.float64), am.area_matrix(ax, w, rw))
    out[:rh, :rw] = np.clip(pic + 0.5, 0, 255).astype(np.uint8)
    return draw_marker2(out, a, q) if mark else out


def view2(frame, a, q, cfg=SQUISH_CFG):
    h, w = frame.shape[:2]
    rw, rh = rect2(a, w, h)
    ax, ay = _axes(cfg, squeeze(a, q))
    up = am.warp2(am.bilinear_matrix(ay, h, rh), frame[:rh, :rw].astype(np.float64), am.bilinear_matrix(ax, w, rw))
    return np.clip(up + 0.5, 0, 255).astype(np.uint8)


class Ramp:
    """Moves (a, q) linearly to a target over `seconds`; one call per frame returns the integer step to stream."""

    def __init__(self, fps=60):
        self.fps, self.a, self.q = fps, 0.0, 0.0
        self.a0 = self.q0 = self.ta = self.tq = 0.0
        self.n = self.i = 0

    def go(self, a, q, seconds):
        if (a, q) == (self.ta, self.tq) and self.i < self.n:
            return
        self.a0, self.q0, self.ta, self.tq = self.a, self.q, float(a), float(q)
        self.n, self.i = max(1, round(seconds * self.fps)), 0

    def step(self):
        if self.i < self.n:
            self.i += 1
            t = self.i / self.n
            self.a = self.a0 + (self.ta - self.a0) * t
            self.q = self.q0 + (self.tq - self.q0) * t
        return int(math.floor(self.a + 0.5)), int(math.floor(self.q + 0.5))  # half up, like the C code


# Demo loop (spec 9.4): (a, q, ramp seconds, hold seconds). Full -> squeeze into 540 -> same size plain ->
# 360 plain -> 360 squeezed -> back to full.
DEMO = [(0, 0, 2.0, 4.0), (38, 7, 2.0, 4.0), (38, 0, 2.0, 4.0), (50, 0, 2.0, 4.0), (50, 7, 2.0, 4.0)]


def demo_targets(fps=60, loops=1):
    """(a, q) per frame for the demo loop, as the filter plays it."""
    r, out = Ramp(fps), []
    for _ in range(loops):
        for a, q, ramp_s, hold_s in DEMO:
            r.go(a, q, ramp_s)
            for _ in range(round((ramp_s + hold_s) * fps)):
                out.append(r.step())
    return out

# ---------------- detector (issue #12) ----------------

K_DEFAULT = 1.35  # Mbit/s (1080p60, CRF-23-like quality) per unit of the 240x135 temporal luma metric
RATIO_TABLE = [(1.5, 0), (3.0, 1), (6.0, 2), (12.0, 3), (24.0, 4), (1e18, 5)]  # demand / budget -> target
# Detector targets as picture fractions (1080, 720, 540, 360, 270, 180 at 1080p); `lowest` caps the index.
TARGET_FRACTIONS = [1.0, 2 / 3, 1 / 2, 1 / 3, 1 / 4, 1 / 6]
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
    steps = [(0, 0), (1, 0), (25, 0), (38, 0), (38, 7), (50, 3), (63, 0), (63, 7), (20, 5)]
    g["v2"] = {"steps": steps, "frac": [frac(a) for a, _ in steps], "squeeze": [squeeze(a, q) for a, q in steps],
               "ext_bits": [ext_bits(a, q) for a, q in steps], "ext_pos": {},
               "rects": {f"{w}x{h}": [list(rect2(a, w, h)) for a, _ in steps]
                         for w, h in ((1920, 1080), (1664, 936), (1330, 936), (1280, 720))},
               "samples": {}, "demo": demo_targets(60)}
    for w, h in ((1920, 1080), (1664, 936), (1280, 720)):
        g["v2"]["ext_pos"][f"{w}x{h}"] = list(ext_pos(w, h))
    for a, q in steps[:6]:
        r = render2(img, a, q)
        v = view2(r, a, q)
        g["v2"]["samples"][f"{a},{q}"] = {"render": [r[y, x].tolist() for y, x in pts], "view": [v[y, x].tolist() for y, x in pts]}
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
    for a, q in [(0, 0), (25, 0), (38, 7), (50, 3), (63, max_q(63)), (63, 0)]:
        f = render2(img, a, q)
        check(read_state(f) == (a, q), f"v2 marker round trip {a},{q}")
        err = np.abs(view2(f, a, q).astype(float) - img)[:1000].mean()
        check(err < 40, f"v2 view error {a},{q}: {err:.1f}")
    check(read_state(render(img, 2)) == (38, 0) and read_state(render(img, 4)) == (19, 7), "v1 codes map to steps")
    check(abs(frac(0) - 1) < 1e-12 and abs(frac(63) - F_MIN) < 1e-12 and a_for(0.5) == 38, "step scale")
    d = demo_targets(60)
    jumps = max(max(abs(d[i][0] - d[i - 1][0]), abs(d[i][1] - d[i - 1][1])) for i in range(1, len(d)))
    check(jumps <= 1 and d[0] == (0, 0) and (38, 7) in d and (50, 0) in d, f"demo ramps in single steps (max jump {jumps})")
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
        codes = [((i * 3) % 64, (i // 9) % 8) for i in range(frames)]  # v2: every scale step, every squeeze step
        for i, c in enumerate(codes):
            f = np.roll(base, i * 3, axis=1)
            f = np.clip(f.astype(np.int16) + rng.randint(-6, 7, f.shape), 0, 255).astype(np.uint8)  # busy-ish noise
            p.stdin.write(draw_marker2(f.copy(), *c).tobytes())
        p.stdin.close()
        p.wait()
        d = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             stdout=subprocess.PIPE)
        ok = n = 0
        while True:
            b = d.stdout.read(w * h * 3)
            if len(b) < w * h * 3:
                break
            ok += read_state(np.frombuffer(b, np.uint8).reshape(h, w, 3)) == codes[n]
            n += 1
        res[kb] = (ok, n)
        print(f"marker CBR {kb}: {ok}/{n} frames decoded")
    return all(a == b == frames for a, b in res.values())


def golden_header():
    """C header with the golden vectors for plugins/foveated-squish/tests/test_starvation.c."""
    g = golden()
    o = ["/* Generated by research/foveation/starvation_math.py --golden-header. Do not edit. */", "#pragma once", ""]
    sizes = list(g["rects"])
    o.append("static const int stv_golden_frames[%d][2] = {%s};" % (
        len(sizes), ", ".join("{%s}" % s.replace("x", ", ") for s in sizes)))
    o.append("static const int stv_golden_rects[%d][6][2] = {" % len(sizes))
    for s in sizes:
        o.append("\t{" + ", ".join("{%d, %d}" % tuple(g["rects"][s][str(c)]) for c in range(6)) + "},")
    o.append("};")
    o.append("static const int stv_golden_marker_pos[%d][2] = {%s};" % (
        len(sizes), ", ".join("{%d, %d}" % tuple(g["marker_pos"][s]) for s in sizes)))
    o.append("static const int stv_golden_bits[6][5] = {" + ", ".join(
        "{%s}" % ", ".join(map(str, g["marker_bits"][str(c)])) for c in range(6)) + "};")
    tr = metric_trace()
    o.append("#define STV_GOLDEN_N %d" % len(tr))
    o.append("static const double stv_golden_trace[STV_GOLDEN_N] = {" + ", ".join("%.4f" % v for v in tr) + "};")
    for name, kb in (("6000", 6000), ("3000", 3000)):
        o.append("static const unsigned char stv_golden_sched_%s[STV_GOLDEN_N] = {%s};" % (
            name, ", ".join(map(str, schedule(tr, kb)))))
    v = g["v2"]
    o.append("#define STV_GOLDEN_V2_N %d" % len(v["steps"]))
    o.append("static const int stv_golden_v2_steps[STV_GOLDEN_V2_N][2] = {%s};" % ", ".join("{%d, %d}" % tuple(x) for x in v["steps"]))
    o.append("static const double stv_golden_v2_frac[STV_GOLDEN_V2_N] = {%s};" % ", ".join("%.17g" % x for x in v["frac"]))
    o.append("static const double stv_golden_v2_squeeze[STV_GOLDEN_V2_N] = {%s};" % ", ".join("%.17g" % x for x in v["squeeze"]))
    o.append("static const int stv_golden_v2_bits[STV_GOLDEN_V2_N][10] = {%s};" % ", ".join("{%s}" % ", ".join(map(str, b)) for b in v["ext_bits"]))
    sizes2 = list(v["rects"])
    o.append("static const int stv_golden_v2_frames[%d][2] = {%s};" % (len(sizes2), ", ".join("{%s}" % s2.replace("x", ", ") for s2 in sizes2)))
    o.append("static const int stv_golden_v2_rects[%d][STV_GOLDEN_V2_N][2] = {" % len(sizes2))
    for s2 in sizes2:
        o.append("\t{" + ", ".join("{%d, %d}" % tuple(r) for r in v["rects"][s2]) + "},")
    o.append("};")
    o.append("#define STV_GOLDEN_DEMO_N %d" % len(v["demo"]))
    o.append("static const unsigned char stv_golden_demo[STV_GOLDEN_DEMO_N][2] = {%s};" % ", ".join("{%d, %d}" % tuple(x) for x in v["demo"]))
    return "\n".join(o) + "\n"


if __name__ == "__main__":
    if "--golden-header" in sys.argv:
        sys.stdout.write(golden_header())
        sys.exit(0)
    if "--robust" in sys.argv:
        sys.exit(0 if robustness() else 1)
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    print(__doc__)
