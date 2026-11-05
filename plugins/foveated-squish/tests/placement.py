"""Placement check for sharp regions (atlas), run by smoke-test.sh.

A known input (grey, a red corner region bottom-right, a green region top-left)
goes through the real filter; the streamed frame must put each colour where the
atlas layout says: picture orientation kept, the first region at the top of the
strip, and the gutter repeating the slot edge (no bleed from the picture).

  placement.py settings          print the region settings for the filter
  placement.py make              write the known input frames (RGBA) to stdout
  placement.py check FILE WxH    check the filter output, print ok / FAIL lines
"""
import sys

import numpy as np

W, H, N = 320, 180, 12          # input frame size and count (same as smoke-test.sh)
SETTINGS = ("atlas_count=2 atlas_0_x0=80 atlas_0_y0=85 atlas_0_x1=100 atlas_0_y1=100 atlas_0_scale=100 "
            "atlas_1_x0=0 atlas_1_y0=0 atlas_1_x1=40 atlas_1_y1=40 atlas_1_scale=100")
GREY, RED, GREEN = (128, 128, 128), (255, 0, 0), (0, 255, 0)
BOX_W = 0.8                     # ATLAS_BOX_W in squish-filter.c
GUTTER = 4                      # ATLAS_GUTTER in atlas-math.h


def close(px, colour):
    return int(np.abs(px[:3].astype(int) - np.array(colour)).max()) <= 60


def make():
    img = np.zeros((H, W, 4), np.uint8)
    img[..., :3] = GREY
    img[..., 3] = 255
    img[int(0.85 * H):, int(0.8 * W):, :3] = RED      # region 0: bottom-right corner
    img[:int(0.4 * H), :int(0.4 * W), :3] = GREEN      # region 1: top-left square
    sys.stdout.buffer.write(np.tile(img, (N, 1, 1, 1)).tobytes())


def check(path, size):
    ow, oh = (int(v) for v in size.split("x"))
    frame = np.fromfile(path, np.uint8)[: ow * oh * 4].reshape(oh, ow, 4)
    pw = round(BOX_W * ow) & ~1
    results = []

    def expect(name, ok):
        results.append((name, bool(ok)))

    col = pw + 20                                       # inside the first slot
    red = [r for r in range(oh) if close(frame[r, col], RED)]
    green = [r for r in range(oh) if close(frame[r, col], GREEN)]
    expect("corner region present in strip", red)
    expect("regions in config order (corner slot above the second)", red and green and max(red) < min(green))
    expect("first slot starts 4 px below the top", red and abs(min(red) - GUTTER) <= 2)
    expect("gutter repeats slot edge (no picture bleed)",
           red and all(close(frame[r, pw + 2], RED) for r in range(min(red) + 2, max(red) - 1)))
    expect("picture top-left shows the top-left region", close(frame[GUTTER, GUTTER], GREEN))

    failed = [name for name, ok in results if not ok]
    if failed:  # diagnostics: where the colours really are
        def runs(rows):
            out, start, prev = [], None, None
            for r in rows:
                if start is None:
                    start = prev = r
                elif r == prev + 1:
                    prev = r
                else:
                    out.append((start, prev)); start = prev = r
            if start is not None:
                out.append((start, prev))
            return out
        print(f"diag frame {ow}x{oh} picture width {pw}")
        print(f"diag red rows in strip column {col}: {runs(red)}")
        print(f"diag green rows in strip column {col}: {runs(green)}")
        for name, colour in [("red", RED), ("green", GREEN)]:
            ys, xs = np.nonzero(np.all(np.abs(frame[:, pw:, :3].astype(int) - np.array(colour)) <= 60, axis=2))
            if len(ys):
                print(f"diag {name} box in strip: x {xs.min() + pw}..{xs.max() + pw}, y {ys.min()}..{ys.max()}, {len(ys)} px")
            else:
                print(f"diag {name} box in strip: none")
        for r, c in [(4, 4), (oh // 2, 4), (oh - 8, 4), (4, pw - 8), (oh - 8, pw - 8), (oh // 2, pw // 2)]:
            print(f"diag picture px row {r} col {c}: {frame[r, c, :3].tolist()}")
        for r, c in [(4, pw + 2), (4, pw + 20), (oh - 8, pw + 20), (oh - 8, ow - 4)]:
            print(f"diag strip px row {r} col {c}: {frame[r, c, :3].tolist()}")
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
