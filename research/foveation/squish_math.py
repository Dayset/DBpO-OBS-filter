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
  FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.7778
  r = squished/source size, c = half-size of the sharp center, f = focus
  position, p = curve (1 linear; <1 squeeze ramps up fast; >1 near-center stays
  sharper, edges squeezed harder), ar = source aspect ratio.
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

    def inverse(self, u, iters=8):
        """Squished position u in [0,1] -> source position x (Newton, like the shader)."""
        u = np.asarray(u, dtype=np.float64)
        x = u.copy()
        for _ in range(iters):
            fx = self.forward(x) - u
            x = np.clip(x - fx / (self.density(x) / self.r), 0, 1)
        return x


@dataclass
class Squish:
    x: Axis
    y: Axis
    ar: float = 16 / 9

    def to_string(self):
        return (f"FSQ1:rx={self.x.r:.4g},ry={self.y.r:.4g},cx={self.x.c:.4g},cy={self.y.c:.4g},"
                f"fx={self.x.f:.4g},fy={self.y.f:.4g},px={self.x.p:.4g},py={self.y.p:.4g},ar={self.ar:.5g}")

    @staticmethod
    def parse(s):
        s = s.strip()
        if not s.startswith("FSQ1:"):
            raise ValueError("not an FSQ1 string")
        kv = dict(item.split("=") for item in s[5:].split(","))
        g = lambda k, d: float(kv.get(k, d))  # noqa: E731
        return Squish(Axis(g("rx", 0.75), g("cx", 0.15), g("fx", 0.5), g("px", 1)),
                      Axis(g("ry", 0.75), g("cy", 0.15), g("fy", 0.5), g("py", 1)), g("ar", 16 / 9))
