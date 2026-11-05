"""Placement check for sharp regions (atlas), run by smoke-test.sh.

A known input (grey, a red region bottom-right) goes through the real filter.
The Python reference (research/foveation/atlas_math.py) gives the slot the
region must occupy in the streamed frame; the output must show the region
there, with its gutter repeating the slot edge and nothing outside the slot.

  placement.py settings          print the region settings for the filter
  placement.py make              write the known input frames (RGBA) to stdout
  placement.py check FILE WxH    check the filter output, print ok / FAIL lines
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "research", "foveation"))
from atlas_math import Atlas, layout, set_source  # noqa: E402

W, H, N = 320, 180, 12          # input frame size and count (same as smoke-test.sh)
SQUISH = "FSQ1:rx=0.75,ry=0.75,cx=0.3,cy=0.3,fx=0.5,fy=0.5,px=1,py=1,ar={ar:.4f},ox=0,oy=0,bw=0.8,bh=1"
REGION = "gx=0.800,gy=0.850,gz=1.000,gv=1.000,gs=1.000"
# Values need a decimal point: the harness stores plain numbers as ints and the filter reads doubles.
SETTINGS = "atlas_count=1 atlas_0_x0=80.0 atlas_0_y0=85.0 atlas_0_x1=100.0 atlas_0_y1=100.0 atlas_0_scale=100.0"
GREY, RED = (128, 128, 128), (255, 0, 0)
GUTTER = 4                      # ATLAS_GUTTER in atlas-math.h
TOL = 2                         # pixels of slack on the slot edges


def close(px, colour):
    return int(np.abs(px[:3].astype(int) - np.array(colour)).max()) <= 60


def make():
    img = np.zeros((H, W, 4), np.uint8)
    img[..., :3] = GREY
    img[..., 3] = 255
    # The region: bottom-right corner. Red extends past it, so its left and top
    # edges are solid red, not a grey/red blend (the gutter repeats those pixels).
    img[int(0.7 * H):, int(0.6 * W):, :3] = RED
    sys.stdout.buffer.write(np.tile(img, (N, 1, 1, 1)).tobytes())


def expected_slot(ow, oh):
    atlas = set_source(Atlas.parse(SQUISH.format(ar=ow / oh) + "," + REGION), ow, oh)
    _, _, slots = layout(atlas, ow, oh, ow, oh)
    x, y, w, h, _ = slots[0]
    return x, y, w, h


def check(path, size):
    ow, oh = (int(v) for v in size.split("x"))
    frame = np.fromfile(path, np.uint8)[: ow * oh * 4].reshape(oh, ow, 4)
    x, y, w, h = expected_slot(ow, oh)
    results = []

    def expect(name, ok):
        results.append((name, bool(ok)))

    mask = np.all(np.abs(frame[:, :, :3].astype(int) - np.array(RED)) <= 60, axis=2)
    fy, fx = np.nonzero(mask)
    if len(fy):
        print(f"diag red anywhere in frame: x {fx.min()}..{fx.max()}, y {fy.min()}..{fy.max()}, {len(fy)} px; "
              f"picture width guess {round(0.8 * ow) & ~1}")
    else:
        print("diag no red anywhere in frame")
    ys, xs = np.nonzero(mask[:, x - GUTTER:])
    xs = xs + x - GUTTER
    expect("region found in the strip", len(ys))
    if len(ys):
        bx0, bx1, by0, by1 = xs.min(), xs.max(), ys.min(), ys.max()
        print(f"diag expected slot+gutter x {x - GUTTER}..{x + w - 1 + GUTTER}, y {y - GUTTER}..{y + h - 1 + GUTTER}; "
              f"measured x {bx0}..{bx1}, y {by0}..{by1}, {len(ys)} px")
        gx0, gy0, gx1, gy1 = x - GUTTER, y - GUTTER, x + w - 1 + GUTTER, y + h - 1 + GUTTER
        ring = (gx1 - gx0 + 1) * (gy1 - gy0 + 1)
        expect("slot and gutter at the reference position",
               abs(bx0 - gx0) <= TOL and abs(by0 - gy0) <= TOL and abs(bx1 - gx1) <= TOL and abs(by1 - gy1) <= TOL)
        expect("no colour outside slot and gutter (pixel count)", abs(len(ys) - ring) <= 0.05 * ring)
    expect("gutter repeats the slot edge (no picture bleed)",
           all(close(frame[r, x - 2], RED) for r in range(y, y + h)))

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
        print(SETTINGS)
    elif cmd == "make":
        make()
    elif cmd == "check":
        check(sys.argv[2], sys.argv[3])
