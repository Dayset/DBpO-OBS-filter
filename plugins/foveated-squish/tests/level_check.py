"""Starvation level check (#14), run by smoke-test.sh: the real filter, held at each level, against the reference.

A smooth test image goes through the filter at 1920x1080 with no squeeze (source = canvas), starvation on and
"Hold a level" set. The output must equal research/foveation/starvation_math.render2 at that level's step:
picture area-downscaled into the top-left rect, black elsewhere, marker v2 last (mean error < 0.5/255 on the
picture, every marker cell exact, and the marker must decode to the step).

  level_check.py levels                 print the levels to check (detector targets)
  level_check.py settings LEVEL         print the filter settings for one level
  level_check.py make                   write the input frames (RGBA) to stdout
  level_check.py check FILE LEVEL       check the filter output, print ok / FAIL lines (exit 1 on FAIL)
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "research", "foveation"))
import starvation_math as sm  # noqa: E402

W, H, N = 1920, 1080, 4
CFG = "FSQ1:rx=1,ry=1,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778"
LEVELS = [1, 2, 3, 5]


def image():
    y, x = np.mgrid[0:H, 0:W].astype(np.float64)
    img = np.zeros((H, W, 3))
    img[..., 0] = 128 + 100 * np.sin(x / 97.0) * np.cos(y / 71.0)
    img[..., 1] = 255 * x / W
    img[..., 2] = 255 * y / H
    return np.clip(img + 0.5, 0, 255).astype(np.uint8)


def expected(level):
    a = sm.a_for(sm.TARGET_FRACTIONS[level])
    return a, sm.render2(image(), a, 0, CFG)


def main():
    cmd = sys.argv[1]
    if cmd == "levels":
        print(" ".join(str(v) for v in LEVELS))
    elif cmd == "settings":
        print(f"starve_on=true starve_force={int(sys.argv[2])} starve_ramp_down=0.0 starve_ramp_up=0.0 "
              "starve_network=false starve_bitrate=6000.0 starve_squish=false")
    elif cmd == "make":
        rgba = np.concatenate([image(), np.full((H, W, 1), 255, np.uint8)], -1)
        out = sys.stdout.buffer
        for _ in range(N):
            out.write(rgba.tobytes())
    elif cmd == "check":
        level = int(sys.argv[3])
        got = np.fromfile(sys.argv[2], np.uint8)
        if got.size < W * H * 4:
            print(f"FAIL no output ({got.size} bytes)")
            sys.exit(1)
        got = got.reshape(-1, H, W, 4)[-1, ..., :3].astype(np.int32)
        a, ref = expected(level)
        rw, rh = sm.rect2(a, W, H)
        mx, my = sm.ext_pos(W, H)
        pic = np.abs(got[:rh, :rw] - ref[:rh, :rw].astype(np.int32)).mean()
        black = np.ones((H, W), bool)
        black[:rh, :rw] = False
        black[my:my + 32, mx:mx + 160] = False
        outside = got[black].max() if black.any() else 0
        strip = np.abs(got[my:my + 32, mx:mx + 160] - ref[my:my + 32, mx:mx + 160].astype(np.int32)).max()
        state = sm.read_state(got.astype(np.uint8))
        fails = []
        if pic >= 0.5:
            fails.append(f"picture mean error {pic:.3f}/255")
        if outside > 1:
            fails.append(f"outside the picture not black (max {outside})")
        if strip > 1:
            fails.append(f"marker cells differ (max {strip})")
        if state != (a, 0):
            fails.append(f"marker reads {state}, sent {(a, 0)}")
        print(("ok" if not fails else "FAIL " + "; ".join(fails)) +
              f" level {level}: step a={a}, rect {rw}x{rh}, picture mean error {pic:.3f}/255")
        sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
