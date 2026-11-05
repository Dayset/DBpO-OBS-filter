"""Placement check for sharp regions (atlas), run by smoke-test.sh.

A known input goes through the real filter. Each region has its own colour
(a corner HUD in red, a square webcam in green, on grey). The Python reference
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

W, H, N = 320, 180, 12          # input frame size and count (same as smoke-test.sh)
CANVAS = (640, 360)             # two regions fit this canvas with the default squish
SQUISH = "FSQ1:rx=0.75,ry=0.75,cx=0.3,cy=0.3,fx=0.5,fy=0.5,px=0.5,py=0.5,ar={ar:.4f},ox=0,oy=0,bw=0.8,bh=1"
# (name, colour, region in percent of the source: x0 y0 x1 y1, scale in percent, python key)
REGIONS = [
    ("hud", (255, 0, 0), (80.0, 85.0, 100.0, 100.0), 100.0, "gx=0.800,gy=0.850,gz=1.000,gv=1.000,gs=1.000"),
    ("webcam", (0, 255, 0), (0.0, 0.0, 12.0, 100.0 * 0.12 * CANVAS[0] / CANVAS[1]), 100.0,
     "hx=0.000,hy=0.000,hz=0.120,hv={v:.4f},hs=1.000".format(v=0.12 * CANVAS[0] / CANVAS[1])),
]
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
    img = np.zeros((H, W, 4), np.uint8)
    img[..., :3] = GREY
    img[..., 3] = 255
    # Each colour covers its region and a little more, so the edges are solid colour.
    img[int(0.70 * H):, int(0.60 * W):, :3] = REGIONS[0][1]
    img[:int(0.30 * H), :int(0.20 * W), :3] = REGIONS[1][1]
    sys.stdout.buffer.write(np.tile(img, (N, 1, 1, 1)).tobytes())


def expected_slots(ow, oh):
    cfg = SQUISH.format(ar=ow / oh) + "," + ",".join(r[4] for r in REGIONS)
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
    if cmd == "settings":
        print(settings())
    elif cmd == "canvas":
        print(f"{CANVAS[0]}x{CANVAS[1]}")
    elif cmd == "make":
        make()
    elif cmd == "check":
        check(sys.argv[2], sys.argv[3])
