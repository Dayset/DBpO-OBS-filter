"""Reference math for the sharp-region atlas (issue #3, spec: plugins/foveated-squish/docs/atlas-spec.md).

Builds on squish_math.py (the plugin's exact squish mapping). Regions are
user-chosen rectangles of the source that reach viewers at a larger scale
than the squished picture gives them. They are packed into a strip beside the
squished picture as "slots", each with a 4 px edge-clamped gutter.

Config string: the FSQ1 string from squish_math.py plus up to four regions.
A region is five pairs, one letter prefix per region (g, h, i, j) and one
field letter (x = x0, y = y0, z = x1, v = y1, s = scale):
  gx=0.800,gy=0.850,gz=1.000,gv=1.000,gs=1.000
Keys stay two lowercase letters, so older parsers see valid pairs and ignore them.

Run the self test with: python3 research/foveation/atlas_math.py --selftest
"""

import json
import math
import os
import re
import sys
from dataclasses import dataclass, field

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from squish_math import Squish  # noqa: E402

MAX_REGIONS = 4
REGION_PREFIXES = "ghij"
GUTTER = 4  # px around each slot
SNAP = 8  # slot width/height rounded up to a multiple of this
STRIP_CAP = 0.20  # strip may take at most this fraction of the output
REGION_MIN = 0.02
REGION_MAX = 0.5

_REGION_KEY = re.compile(r"(?<![a-z])([ghij])([xyzvs])=([-0-9.eE]+)")


@dataclass
class Region:
    x0: float
    y0: float
    x1: float
    y1: float
    s: float = 1.0  # scale: 1.0 = 1:1 with source pixels (before stream scaling)

    def centre(self):
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2


@dataclass
class Atlas:
    squish: Squish
    regions: list = field(default_factory=list)

    # ---------- config string ----------
    def to_string(self):
        s = self.squish.to_string()
        for i, r in enumerate(self.regions):
            p = REGION_PREFIXES[i]
            s += ",{p}x={:.3f},{p}y={:.3f},{p}z={:.3f},{p}v={:.3f},{p}s={:.3f}".format(
                r.x0, r.y0, r.x1, r.y1, r.s, p=p)
        return s

    @staticmethod
    def parse(text):
        text = text.strip()
        squish = Squish.parse(text)
        regions = []
        for i, p in enumerate(REGION_PREFIXES):
            vals = {}
            for m in _REGION_KEY.finditer(text):
                if m.group(1) == p:
                    vals[m.group(2)] = float(m.group(3))
            if not vals:
                continue
            if set(vals) != {"x", "y", "z", "v", "s"}:
                raise ValueError(f"region {p} is missing fields")
            if i != len(regions):
                raise ValueError("regions must be listed without gaps")
            regions.append(Region(vals["x"], vals["y"], vals["z"], vals["v"], vals["s"]))
        return Atlas(squish, regions)

    # ---------- validation and floors ----------
    def floor(self, region):
        """Smallest allowed scale: at least 50% less squish at the region centre (spec 2)."""
        cx, cy = region.centre()
        dx = float(self.squish.x.density(cx))
        dy = float(self.squish.y.density(cy))
        return max((dx + 1) / 2, (dy + 1) / 2)

    def validate(self):
        if len(self.regions) > MAX_REGIONS:
            raise ValueError("too many regions")
        for r in self.regions:
            if not (0 <= r.x0 < r.x1 <= 1 and 0 <= r.y0 < r.y1 <= 1):
                raise ValueError("region outside the source")
            for axis_len in (r.x1 - r.x0, r.y1 - r.y0):
                if not (REGION_MIN <= axis_len <= REGION_MAX):
                    raise ValueError("region size out of range")
        for i, a in enumerate(self.regions):
            for b in self.regions[i + 1:]:
                if a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1:
                    raise ValueError("regions overlap")
        if self.squish.box is None:
            if self.regions:
                raise ValueError("regions need a boxed layout (bw or bh < 1)")
            return
        _, _, bw, bh = self.squish.box
        if bw < 1 and bh < 1:
            raise ValueError("box must shrink one axis only")
        if bw < 1 and 1 - bw > STRIP_CAP or bh < 1 and 1 - bh > STRIP_CAP:
            raise ValueError("strip larger than the cap")


# ---------- layout ----------

def layout(atlas, out_w, out_h, src_w, src_h):
    """Deterministic shelf packing (spec 3). Returns (picture_rect, strip_rect, slots).

    picture_rect = (0, 0, pw, ph) in output px; strip_rect = (sx, sy, sw, sh);
    slots = list of (x, y, w, h, scale) with x, y the slot's top-left (after the gutter).
    Raises if the regions cannot fit above the floor.
    """
    atlas.validate()
    _, _, bw, bh = atlas.squish.box if atlas.squish.box else (0, 0, 1, 1)
    pw = int(round(bw * out_w)) & ~1
    ph = int(round(bh * out_h)) & ~1
    if bw < 1:
        strip = (pw, 0, out_w - pw, out_h)
    elif bh < 1:
        strip = (0, ph, out_w, out_h - ph)
    else:
        strip = (0, 0, 0, 0)
    if not atlas.regions:
        return (0, 0, pw, ph), strip, []

    k_w = out_w / src_w
    k_h = out_h / src_h
    scale = 1.0
    while True:
        slots = _shelf(atlas, strip, k_w, k_h, scale)
        if slots is not None:
            return (0, 0, pw, ph), strip, slots
        scale *= 0.95
        for r in atlas.regions:
            if scale < atlas.floor(r):
                raise ValueError("regions do not fit above the density floor")


def _shelf(atlas, strip, k_w, k_h, factor):
    sx, sy, sw, sh = strip
    slots = []
    if sw == 0 and sh == 0:
        return None
    cx, cy, row_h = sx, sy, 0
    for r in atlas.regions:
        s = max(min(r.s * factor, 1.0), atlas.floor(r))
        w = math.floor((r.x1 - r.x0) * src_dim(atlas, "w") * s * k_w)
        h = math.floor((r.y1 - r.y0) * src_dim(atlas, "h") * s * k_h)
        w = max(SNAP, -(-w // SNAP) * SNAP)
        h = max(SNAP, -(-h // SNAP) * SNAP)
        cw, ch = w + 2 * GUTTER, h + 2 * GUTTER
        if cw > sw or ch > sh:
            return None
        if cx + cw > sx + sw:  # new row
            cx = sx
            cy += row_h
            row_h = 0
        if cy + ch > sy + sh:
            return None
        slots.append((cx + GUTTER, cy + GUTTER, w, h, s))
        cx += cw
        row_h = max(row_h, ch)
    return slots


def src_dim(atlas, which):
    # Source dimensions are carried on the atlas by the frame helpers; layout
    # callers set them via set_source(). Default: 1 (region sizes given as pixels).
    return getattr(atlas, "_src_w" if which == "w" else "_src_h", 1)


def set_source(atlas, src_w, src_h):
    atlas._src_w = src_w
    atlas._src_h = src_h
    return atlas


# ---------- resampling (same matrices as squish_profiles.py) ----------

def area_matrix(axis, n_src, n_out):
    edges = axis.inverse(np.arange(n_out + 1) / n_out) * n_src
    A = np.zeros((n_out, n_src))
    for u in range(n_out):
        a, b = edges[u], edges[u + 1]
        i0, i1 = int(np.floor(a)), int(np.ceil(b))
        for i in range(max(i0, 0), min(i1, n_src)):
            A[u, i] = max(0.0, min(b, i + 1) - max(a, i))
        A[u] /= A[u].sum()
    return A


def bilinear_matrix(axis, n_src, n_out):
    pos = axis.forward((np.arange(n_src) + 0.5) / n_src) * n_out - 0.5
    pos = np.clip(pos, 0, n_out - 1)
    i0 = np.floor(pos).astype(int)
    i1 = np.minimum(i0 + 1, n_out - 1)
    w = pos - i0
    B = np.zeros((n_src, n_out))
    B[np.arange(n_src), i0] += 1 - w
    B[np.arange(n_src), i1] += w
    return B


def warp2(rows, img, cols):
    return np.einsum("vx,uxc->uvc", cols, np.einsum("uy,yxc->uxc", rows, img, optimize=True), optimize=True)


def resize_linear(img, out_h, out_w):
    """Plain area-average resize (no squish) for slot content."""
    rows = area_matrix(Squish.parse("FSQ1:rx=1,ry=1,cx=0.5,cy=0.5").y, img.shape[0], out_h)
    cols = area_matrix(Squish.parse("FSQ1:rx=1,ry=1,cx=0.5,cy=0.5").x, img.shape[1], out_w)
    return warp2(rows, img, cols)


# ---------- frames ----------

def squish_frame_with_atlas(src, cfg_text, out_w, out_h, blank_duplicate=True):
    """Streamed frame: squished picture in its box, slots + gutters in the strip."""
    atlas = set_source(Atlas.parse(cfg_text), src.shape[1], src.shape[0])
    (_, _, pw, ph), _, slots = layout(atlas, out_w, out_h, src.shape[1], src.shape[0])
    sq = atlas.squish
    rows = area_matrix(sq.y, src.shape[0], ph)
    cols = area_matrix(sq.x, src.shape[1], pw)
    picture = warp2(rows, src, cols)
    frame = np.zeros((out_h, out_w, src.shape[2]))
    frame[:ph, :pw] = picture

    for r in atlas.regions:
        if blank_duplicate:
            u0, u1 = _picture_span(sq.x, r.x0, r.x1, pw)
            v0, v1 = _picture_span(sq.y, r.y0, r.y1, ph)
            mean = src[int(r.y0 * src.shape[0]):int(math.ceil(r.y1 * src.shape[0])),
                       int(r.x0 * src.shape[1]):int(math.ceil(r.x1 * src.shape[1]))].mean(axis=(0, 1))
            frame[v0:v1, u0:u1] = mean

    for r, (x, y, w, h, _) in zip(atlas.regions, slots):
        crop = src[int(r.y0 * src.shape[0]):int(math.ceil(r.y1 * src.shape[0])),
                   int(r.x0 * src.shape[1]):int(math.ceil(r.x1 * src.shape[1]))]
        slot = resize_linear(crop, h, w)
        gutter = np.pad(slot, ((GUTTER, GUTTER), (GUTTER, GUTTER), (0, 0)), mode="edge")
        frame[y - GUTTER:y + h + GUTTER, x - GUTTER:x + w + GUTTER] = gutter
    return frame, slots


def _picture_span(axis, a, b, n):
    """Picture pixel span [i0, i1) that a source interval [a, b] occupies."""
    u0 = int(math.floor(axis.forward(a) * n))
    u1 = int(math.ceil(axis.forward(b) * n))
    return max(u0, 0), min(u1, n)


def unsquish_with_atlas(frame, cfg_text, view_w, view_h, src_w, src_h):
    """What the viewer sees: unsquish the picture, paste slots back over their regions."""
    atlas = set_source(Atlas.parse(cfg_text), src_w, src_h)
    (_, _, pw, ph), _, slots = layout(atlas, frame.shape[1], frame.shape[0], src_w, src_h)
    sq = atlas.squish
    picture = frame[:ph, :pw]
    rows = bilinear_matrix(sq.y, view_h, ph)
    cols = bilinear_matrix(sq.x, view_w, pw)
    view = warp2(rows, picture, cols).copy()
    for r, (x, y, w, h, _) in zip(atlas.regions, slots):
        vx0, vx1 = int(round(r.x0 * view_w)), int(round(r.x1 * view_w))
        vy0, vy1 = int(round(r.y0 * view_h)), int(round(r.y1 * view_h))
        ys = np.clip((np.arange(vy0, vy1) - vy0 + 0.5) * h / max(vy1 - vy0, 1), 0, h - 1).astype(int)
        xs = np.clip((np.arange(vx0, vx1) - vx0 + 0.5) * w / max(vx1 - vx0, 1), 0, w - 1).astype(int)
        view[vy0:vy1, vx0:vx1] = frame[y + ys][:, x + xs]
    return view


# ---------- golden vectors ----------

GOLDEN_CASES = [
    "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778",
    "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778,ox=0,oy=0,bw=0.800,bh=1",
    "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778,ox=0,oy=0,bw=0.800,bh=1,gx=0.800,gy=0.850,gz=1.000,gv=1.000,gs=1.000",
    "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778,ox=0,oy=0,bw=1,bh=0.800,gx=0.000,gy=0.000,gz=0.250,gv=0.200,gs=0.800",
    "FSQ1:rx=0.6,ry=0.6,cx=0.10,cy=0.10,fx=0.5,fy=0.5,px=0.5,py=0.5,ar=1.778,ox=0,oy=0,bw=0.800,bh=1,gx=0.100,gy=0.100,gz=0.300,gv=0.250,gs=1.000,hx=0.700,hy=0.700,hz=0.900,hv=0.950,hs=1.000",
    "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778,ox=0,oy=0,bw=0.800,bh=1,gx=0.050,gy=0.050,gz=0.150,gv=0.150,gs=0.900,hx=0.820,hy=0.820,hz=0.980,hv=0.980,hs=0.900",
    "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778,ox=0,oy=0,bw=0.800,bh=1,gx=0.800,gy=0.850,gz=1.000,gv=1.000,gs=1.000,hx=0.000,hy=0.000,hz=0.200,hv=0.150,hs=1.000",
    "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778,ox=0,oy=0,bw=0.800,bh=1,gx=0.500,gy=0.500,gz=0.600,gv=0.600,gs=1.000,hx=0.100,hy=0.100,hz=0.200,hv=0.200,hs=1.000,ix=0.300,iy=0.300,iz=0.400,iv=0.400,is=1.000",
    "FSQ1:rx=0.7,ry=0.7,cx=0.12,cy=0.12,fx=0.4,fy=0.6,px=1,py=1,ar=1.778,ox=0,oy=0,bw=0.800,bh=1,gx=0.650,gy=0.050,gz=0.830,gv=0.300,gs=0.950",
    "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778,ox=0,oy=0,bw=0.800,bh=1,gx=0.020,gy=0.020,gz=0.220,gv=0.220,gs=0.700",
]


def golden_vectors():
    out = []
    for text in GOLDEN_CASES:
        atlas = set_source(Atlas.parse(text), 2560, 1440)
        entry = {"config": text, "round_trip": Atlas.parse(atlas.to_string()).to_string() == atlas.to_string(),
                 "regions": [[r.x0, r.y0, r.x1, r.y1, r.s] for r in atlas.regions],
                 "floors": [round(atlas.floor(r), 6) for r in atlas.regions]}
        if atlas.regions:
            _, _, slots = layout(atlas, 1920, 1080, 2560, 1440)
            entry["slots_1080p"] = [list(s) for s in slots]
        out.append(entry)
    return out


# ---------- self test ----------

def selftest():
    rng = np.random.default_rng(1)
    failures = []

    # 1. golden round trip and 0-region identity with squish_math
    for text in GOLDEN_CASES:
        a = Atlas.parse(text)
        if Atlas.parse(a.to_string()).to_string() != a.to_string():
            failures.append(f"round trip: {text}")
    plain = Atlas.parse(GOLDEN_CASES[0])
    if plain.to_string() != plain.squish.to_string():
        failures.append("0-region string changed")

    # 2. slots never overlap each other or the picture
    for text in GOLDEN_CASES[2:]:
        atlas = set_source(Atlas.parse(text), 2560, 1440)
        (px0, py0, pw, ph), strip, slots = layout(atlas, 1920, 1080, 2560, 1440)
        rects = [(x - GUTTER, y - GUTTER, w + 2 * GUTTER, h + 2 * GUTTER) for x, y, w, h, _ in slots]
        for i, (ax, ay, aw, ah) in enumerate(rects):
            if ax < pw and ay < ph and ax + aw > 0 and ay + ah > 0 and pw > 0:
                if ax < pw:  # gutter may not reach into the picture
                    failures.append(f"slot {i} overlaps picture: {text[:40]}")
            for bx, by, bw_, bh_ in rects[i + 1:]:
                if ax < bx + bw_ and bx < ax + aw and ay < by + bh_ and by < ay + ah:
                    failures.append(f"slots overlap: {text[:40]}")

    # 3. reproduction of a region at scale 1.0 with no encoder (smooth source)
    h_src, w_src = 720, 1280
    yy, xx = np.mgrid[0:h_src, 0:w_src]
    src = np.stack([xx / w_src, yy / h_src, 0.5 * np.ones_like(xx, dtype=float)], axis=-1)
    text = "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778,ox=0,oy=0,bw=0.800,bh=1,gx=0.800,gy=0.850,gz=1.000,gv=1.000,gs=1.000"
    frame, slots = squish_frame_with_atlas(src, text, 1920, 1080, blank_duplicate=False)
    view = unsquish_with_atlas(frame, text, w_src, h_src, w_src, h_src)
    r0, r1, c0, c1 = int(0.85 * h_src), h_src, int(0.8 * w_src), w_src
    err = np.abs(view[r0:r1, c0:c1] - src[r0:r1, c0:c1]).mean() * 255
    report = {"region_mean_error_255": round(float(err), 3)}
    if err > 0.5:
        failures.append(f"region error {err:.3f}/255 above 0.5")

    print(json.dumps({"ok": not failures, "failures": failures, **report}, indent=2))
    return not failures


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    if "--golden" in sys.argv:
        print(json.dumps(golden_vectors(), indent=2))
