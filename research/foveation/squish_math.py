"""Reference math for Foveated Squish (shared by the OBS plugin and the browser
extension; both are tested against this file).

Per axis, in normalized coordinates (source x in [0, 1], squished u in [0, 1]):

  density d(x) = squished pixels per source pixel
             = 1                                   within the sharp center
             = 1 - (1 - dmin) * t^p                in the periphery,
  where t in [0, 1] runs from the center edge to the frame edge on each side.

  U(x) = (1 / r) * integral_0^x d       (r = squished size / source size)

The periphery integral is closed-form, so dmin follows from r directly:
  r = 1 - (1 - dmin) * (Lleft + Lright) / (p + 1)
  dmin = 1 - (1 - r) * (p + 1) / (Lleft + Lright)
with Lleft/Lright the periphery lengths (fractions of the axis). U is strictly
increasing (d >= dmin > 0), so the inverse is well-defined (Newton).

Config string (what the streamer shares):
  FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.7778[,ox=..,oy=..,bw=..,bh=..]
  r = squished/source size, c = half-size of the sharp center, f = focus
  position, p = curve (1 linear; <1 squeeze ramps up fast; >1 near-center stays
  sharper, edges squeezed harder), ar = source aspect ratio; optional box =
  where the squished picture sits in the streamed frame (black bars around it).
"""

from dataclasses import dataclass

import numpy as np

MIN_DENSITY = 0.08


@dataclass
class Axis:
    r: float = 0.75  # squished size / source size
    c: float = 0.15  # half-size of the untouched center (fraction of axis)
    f: float = 0.5  # focus position
    p: float = 1.0  # curve

    def bounds(self):
        lo = max(self.f - self.c, 0.0)
        hi = min(self.f + self.c, 1.0)
        return lo, hi, lo, 1.0 - hi  # center start/end, left/right periphery lengths

    def dmin(self):
        lo, hi, ll, lr = self.bounds()
        total = ll + lr
        if total <= 1e-9:
            return 1.0
        return 1.0 - (1.0 - self.r) * (self.p + 1.0) / total

    def valid(self):
        return self.dmin() >= MIN_DENSITY and 0 < self.r <= 1

    def max_squeeze(self):
        """Smallest r reachable with these c/p (density floor MIN_DENSITY)."""
        lo, hi, ll, lr = self.bounds()
        return 1.0 - (1.0 - MIN_DENSITY) * (ll + lr) / (self.p + 1.0)

    def forward(self, x):
        """Source position x in [0,1] -> squished position u in [0,1]."""
        x = np.asarray(x, dtype=np.float64)
        lo, hi, ll, lr = self.bounds()
        k = (1.0 - self.dmin()) / (self.p + 1.0)

        def periph(t, length):  # integral of density over [0, t*length]
            return length * (t - k * np.power(t, self.p + 1.0))

        left_full = periph(1.0, ll)
        tl = np.clip((lo - x) / max(ll, 1e-12), 0, 1)  # 1 at the left edge
        tr = np.clip((x - hi) / max(lr, 1e-12), 0, 1)
        u = np.where(
            x < lo,
            left_full - periph(tl, ll),
            np.where(x <= hi, left_full + (x - lo), left_full + (hi - lo) + periph(tr, lr)),
        )
        return u / self.r

    def density(self, x):
        x = np.asarray(x, dtype=np.float64)
        lo, hi, ll, lr = self.bounds()
        t = np.where(x < lo, (lo - x) / max(ll, 1e-12), np.where(x > hi, (x - hi) / max(lr, 1e-12), 0.0))
        return 1.0 - (1.0 - self.dmin()) * np.power(np.clip(t, 0, 1), self.p)

    def inverse(self, u, iters=20):
        """Squished position u in [0,1] -> source position x (like the shader and the C code).

        Per zone: the center is linear; in the periphery solve len*(t - k t^(p+1)) = target
        with Newton kept inside a shrinking bracket (bisection when a step leaves it).
        Plain Newton cycles for flat curves with strong squeeze.
        """
        u = np.asarray(u, dtype=np.float64)
        lo_c, hi_c, ll, lr = self.bounds()
        p = self.p
        k = (1.0 - self.dmin()) / (p + 1.0)

        def solve(target, length):
            target = np.asarray(target, dtype=np.float64)
            if length <= 1e-12:
                return np.zeros_like(target)
            lo, hi = np.zeros_like(target), np.ones_like(target)
            t = np.clip(target / length, 0, 1)
            for _ in range(iters):
                f = length * (t - k * np.power(t, p + 1.0)) - target
                hi = np.where(f > 0, t, hi)
                lo = np.where(f > 0, lo, t)
                d = length * (1.0 - k * (p + 1.0) * np.power(t, p))
                tn = np.where(d > 1e-12, t - f / np.where(d > 1e-12, d, 1), 0.5 * (lo + hi))
                t = np.where((tn >= lo) & (tn <= hi), tn, 0.5 * (lo + hi))
            return t

        v = np.clip(u, 0, 1) * self.r
        left_full = ll * (1.0 - k)
        x_left = np.clip(lo_c - ll * solve(left_full - v, ll), 0, 1)
        x_mid = lo_c + (v - left_full)
        x_right = np.clip(hi_c + lr * solve(v - left_full - (hi_c - lo_c), lr), 0, 1)
        return np.where(v < left_full, x_left, np.where(v <= left_full + (hi_c - lo_c), x_mid, x_right))


@dataclass
class Squish:
    x: Axis
    y: Axis
    ar: float = 16 / 9
    box: tuple = None  # (ox, oy, bw, bh): picture rectangle in the streamed frame, None = whole frame

    def to_string(self):
        s = (f"FSQ1:rx={self.x.r:.4g},ry={self.y.r:.4g},cx={self.x.c:.4g},cy={self.y.c:.4g},"
             f"fx={self.x.f:.4g},fy={self.y.f:.4g},px={self.x.p:.4g},py={self.y.p:.4g},ar={self.ar:.5g}")
        if self.box:
            s += ",ox={:.6g},oy={:.6g},bw={:.6g},bh={:.6g}".format(*self.box)
        return s

    @staticmethod
    def parse(s):
        s = s.strip()
        if not s.startswith("FSQ1:"):
            raise ValueError("not an FSQ1 string")
        kv = dict(item.split("=") for item in s[5:].split(","))
        g = lambda k, d: float(kv.get(k, d))  # noqa: E731
        box = None
        if any(k in kv for k in ("ox", "oy", "bw", "bh")):
            box = (g("ox", 0), g("oy", 0), g("bw", 1), g("bh", 1))
        return Squish(Axis(g("rx", 0.75), g("cx", 0.15), g("fx", 0.5), g("px", 1)),
                      Axis(g("ry", 0.75), g("cy", 0.15), g("fy", 0.5), g("py", 1)), g("ar", 16 / 9), box)
