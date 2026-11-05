"""Player controls stay above the unsquished canvas (headless Chromium).

Builds a fake player: video, then controls placed after it in three common ways (plain static
block, absolutely positioned with z-index, absolutely positioned without z-index). Screenshots the
page with the extension active and checks every control is visible and the video area is
drawn by the canvas (not black).

Usage: python3 viewer/unsquish-extension/tests/e2e_controls.py
"""
import http.server
import os
import subprocess
import sys
import tempfile
import threading

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.dirname(HERE)
W, H = 640, 360
CFG = "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778"

PAGE = """<!doctype html><html><head><title>Fake player</title></head>
<body style="margin:0;background:#222">
<div id="player" style="width:640px;height:360px;background:#000">
 <video id="v" src="clip.webm" muted loop autoplay playsinline style="width:100%;height:100%"></video>
 <div id="a" style="height:30px;width:60px;background:#f00;margin-top:-340px;margin-left:10px"></div>
 <div id="b" style="position:absolute;z-index:5;left:100px;top:10px;height:30px;width:60px;background:#0f0"></div>
 <div id="c" style="position:absolute;left:200px;top:10px;height:30px;width:60px;background:#00f"></div>
</div>
<script>
const store = { enabled: true, autoDetect: false, config: "@@CFG@@" };
window.chrome = { storage: {
  sync: { get: (d, cb) => cb({ ...d, ...store }), set: (o, cb) => cb && cb() },
  local: { get: (d, cb) => cb(d), set: (o, cb) => cb && cb() },
  onChanged: { addListener() {} } } };
</script>
<script src="squish.js"></script><script src="content.js"></script></body></html>"""


def main():
    tmp = tempfile.mkdtemp()
    for n in ("squish.js", "content.js"):
        os.symlink(os.path.join(EXT, n), os.path.join(tmp, n))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=30:d=2",
                    "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", os.path.join(tmp, "clip.webm")], check=True)
    open(os.path.join(tmp, "p.html"), "w").write(PAGE.replace("@@CFG@@", CFG))

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: Quiet(*a, directory=tmp))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--use-gl=angle",
                                    "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        page.goto(f"http://127.0.0.1:{srv.server_port}/p.html")
        page.wait_for_function("document.querySelector('canvas[data-dbpo-unsquish]') !== null", timeout=15000)
        page.wait_for_timeout(800)
        shot = os.path.join(tmp, "shot.png")
        page.screenshot(path=shot)
        pos = page.evaluate("""() => Object.fromEntries(['a','b','c'].map(i => {
            const r = document.getElementById(i).getBoundingClientRect();
            return [i, [r.x + r.width / 2, r.y + r.height / 2]]; }))""")
        b.close()
    srv.shutdown()
    img = np.asarray(Image.open(shot).convert("RGB")).astype(int)
    want = {"a": (255, 0, 0), "b": (0, 255, 0), "c": (0, 0, 255)}
    ok = True
    for k, (x, y) in pos.items():
        got = tuple(img[int(y), int(x)])
        good = max(abs(got[i] - want[k][i]) for i in range(3)) < 8
        ok = ok and good
        print(f"control {k} at ({x:.0f},{y:.0f}): {got} {'visible' if good else 'COVERED'}")
    centre = img[H // 2 - 20:H // 2 + 20, W // 2 - 20:W // 2 + 20]
    painted = centre.std() > 5
    print("video area drawn by canvas:", painted)
    ok = ok and painted
    print("PASS" if ok else "FAIL", f"(screenshot {shot})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
