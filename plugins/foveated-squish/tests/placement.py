"""Placement check for sharp regions (atlas), run by smoke-test.sh.

A known input goes through the real filter. Each region has its own colour
(a corner HUD in red, the minimap in green, on grey). The Python reference
(research/foveation/atlas_math.py) gives each slot in the streamed frame; the
output must show each colour in its slot, with its gutter repeating the slot
edge and nothing else in the strip.

  placement.py settings          print the region settings for the filter
  placement.py canvas            print the canvas size the check needs (WxH)
  placement.py make              write the known input frames (RGBA) to stdout
  placement.py check FILE WxH    check the filter output, print ok / FAIL lines
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "research", "foveation"))
from atlas_math import Atlas, layout, set_source  # noqa: E402

N = 2                           # frames (the check reads the first)
CANVAS = (1920, 1080)           # output size the check needs: HUD (0.9) + minimap fit here (native source size)
SOURCE = (1920, 1080)           # the source the region coordinates refer to
SQUISH = "FSQ1:rx=0.75,ry=0.75,cx=0.3,cy=0.3,fx=0.5,fy=0.5,px=0.5,py=0.5,ar={ar:.4f},ox=0,oy=0,bw=0.8,bh=1"


def pct(px, py, pw, ph):
    """Source pixel rect (x, y, w, h) as the percentages the filter's settings use."""
    return (100.0 * px / SOURCE[0], 100.0 * py / SOURCE[1],
            100.0 * (px + pw) / SOURCE[0], 100.0 * (py + ph) / SOURCE[1])


# (name, colour, region in percent of the source: x0 y0 x1 y1, scale in percent, key string for the config)
# The minimap is the user's demo spin: source x 23, y 680, 368 x 368 px.
MINIMAP = pct(23, 680, 368, 368)
ALL_REGIONS = [
    ("hud", (255, 0, 0), (80.0, 85.0, 100.0, 100.0), 90.0, "gx=0.800,gy=0.850,gz=1.000,gv=1.000,gs=0.900"),
    ("minimap", (0, 255, 0), MINIMAP, 100.0,
     "hx={:.6f},hy={:.6f},hz={:.6f},hv={:.6f},hs=1.000".format(*(v / 100 for v in MINIMAP))),
]
# Optional selection by name (hud, minimap), for checking one region at a time: argv after the command.
REGIONS = ALL_REGIONS


def region_key(i, r):
    """Config keys for region i: the prefixes g, h, i, j in order, so any selection stays gap-free."""
    p = "ghij"[i]
    x0, y0, x1, y1 = (v / 100 for v in r[2])
    return f"{p}x={x0:.6f},{p}y={y0:.6f},{p}z={x1:.6f},{p}v={y1:.6f},{p}s={r[3] / 100:.3f}"


def select(names):
    global REGIONS
    if names:
        REGIONS = [r for r in ALL_REGIONS if r[0] in names]
    return REGIONS


GREY = (128, 128, 128)
GUTTER = 4                      # ATLAS_GUTTER in atlas-math.h
TOL = 2                         # pixels of slack on the slot edges


def settings():
    parts = [f"atlas_count={len(REGIONS)}"]
    for i, (_, _, (x0, y0, x1, y1), scale, _) in enumerate(REGIONS):
        parts += [f"atlas_{i}_x0={x0:.4f}", f"atlas_{i}_y0={y0:.4f}", f"atlas_{i}_x1={x1:.4f}",
                  f"atlas_{i}_y1={y1:.4f}", f"atlas_{i}_scale={scale:.1f}"]
    return " ".join(parts)


def close(px, colour):
    return int(np.abs(px[:3].astype(int) - np.array(colour)).max()) <= 60


def make():
    # The input is the canvas size: a real source is the canvas size, and a smaller input would be upscaled.
    W, H = CANVAS
    img = np.zeros((H, W, 4), np.uint8)
    img[..., :3] = GREY
    img[..., 3] = 255
    # Each colour covers its region and a little more, so the edges are solid colour.
    img[int(0.70 * H):, int(0.60 * W):, :3] = ALL_REGIONS[0][1]
    # The minimap's colour runs a few pixels past its region, so the sampled edge is the colour, not grey.
    m = 8
    x0, y0, x1, y1 = MINIMAP
    img[max(0, int(y0 / 100 * H) - m):int(y1 / 100 * H) + m, max(0, int(x0 / 100 * W) - m):int(x1 / 100 * W) + m, :3] = ALL_REGIONS[1][1]
    sys.stdout.buffer.write(np.tile(img, (N, 1, 1, 1)).tobytes())


def expected_slots(ow, oh):
    cfg = SQUISH.format(ar=ow / oh) + "," + ",".join(region_key(i, r) for i, r in enumerate(REGIONS))
    atlas = set_source(Atlas.parse(cfg), ow, oh)
    _, _, slots = layout(atlas, ow, oh, ow, oh)
    return [tuple(s[:4]) for s in slots]


def check(path, size):
    ow, oh = (int(v) for v in size.split("x"))
    frame = np.fromfile(path, np.uint8)[: ow * oh * 4].reshape(oh, ow, 4)
    pw = round(0.8 * ow) & ~1
    results = []
    print(f"diag output {ow}x{oh}, strip starts at x {pw}, file {frame.shape}")

    def expect(name, ok):
        results.append((name, bool(ok)))

    for (name, colour, _, _, _), (x, y, w, h) in zip(REGIONS, expected_slots(ow, oh)):
        strip = frame[:, pw:, :3].astype(int)
        ys, xs = np.nonzero(np.all(np.abs(strip - np.array(colour)) <= 60, axis=2))
        xs = xs + pw
        print(f"diag {name}: {len(ys)} pixels of this colour in the strip"
              + (f", x {xs.min()}..{xs.max()}, y {ys.min()}..{ys.max()}" if len(ys) else ""))
        if len(ys) == 0:
            expect(f"{name}: found in the strip", False)
            continue
        # Only count pixels near this slot: the reference says where it is.
        gx0, gy0, gx1, gy1 = x - GUTTER, y - GUTTER, x + w - 1 + GUTTER, y + h - 1 + GUTTER
        near = (xs >= gx0 - TOL) & (xs <= gx1 + TOL) & (ys >= gy0 - TOL) & (ys <= gy1 + TOL)
        if not near.any():
            expect(f"{name}: colour found near the reference slot", False)
            continue
        bx0, bx1, by0, by1 = xs[near].min(), xs[near].max(), ys[near].min(), ys[near].max()
        ring = (gx1 - gx0 + 1) * (gy1 - gy0 + 1)
        print(f"diag {name}: expected slot+gutter x {gx0}..{gx1}, y {gy0}..{gy1}; "
              f"measured x {bx0}..{bx1}, y {by0}..{by1}, {int(near.sum())} px")
        expect(f"{name}: slot and gutter at the reference position",
               abs(bx0 - gx0) <= TOL and abs(by0 - gy0) <= TOL and abs(bx1 - gx1) <= TOL and abs(by1 - gy1) <= TOL)
        expect(f"{name}: no colour outside slot and gutter (pixel count)",
               abs(int(near.sum()) - ring) <= 0.05 * ring)
        expect(f"{name}: gutter repeats the slot edge (no bleed)",
               all(close(frame[r, x - 2], colour) for r in range(y, y + h)))

    failed = [name for name, ok in results if not ok]
    for name, ok in results:
        print(("ok   " if ok else "FAIL ") + name)
    if failed:
        print("FAIL placement")
        sys.exit(1)
    print("ok placement")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd in ("settings", "check"):
        select(sys.argv[4:] if cmd == "check" else sys.argv[2:])
    if cmd == "settings":
        print(settings())
    elif cmd == "canvas":
        print(f"{CANVAS[0]}x{CANVAS[1]}")
    elif cmd == "make":
        make()
    elif cmd == "check":
        check(sys.argv[2], sys.argv[3])
