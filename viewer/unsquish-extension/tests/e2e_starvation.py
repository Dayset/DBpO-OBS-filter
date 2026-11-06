"""End-to-end check of starvation levels in headless Chromium (issue #15).

Renders a test picture at every level with the Python reference (starvation_math.render), encodes each as a
near-lossless VP9 clip, plays it with content.js injected and compares the WebGL canvas with
starvation_math.view of the same frame. Then plays a clip that switches 0 -> 2 -> 3 -> 5 -> 0 and checks that the
extension decoded every level and changed only along that path (no stray levels = no flicker from bad reads).

Usage: python3 viewer/unsquish-extension/tests/e2e_starvation.py
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(EXT, "..", "..", "research", "foveation"))
import starvation_math as sm  # noqa: E402

W, H = 640, 360
CFG = "FSQ1:rx=1,ry=1,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.7778,sl=1"


def picture():
    y, x = np.mgrid[0:H, 0:W]
    img = np.zeros((H, W, 3))
    img[..., 0] = x / W
    img[..., 1] = y / H
    img[..., 2] = 0.5 + 0.4 * np.sin(x / 9.0) * np.sin(y / 11.0)
    img[(np.abs(x - 0.5 * W) < 12) & (np.abs(y - 0.45 * H) < 12)] = 1
    return (img * 255 + 0.5).astype(np.uint8)


def encode(frames_dir, out, fps=30):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(fps), "-i", os.path.join(frames_dir, "%03d.png"),
                    "-c:v", "libvpx-vp9", "-crf", "4", "-b:v", "0", "-pix_fmt", "yuv444p", out], check=True)


def page_html():
    return f"""<!doctype html><html><head><title>Test stream</title></head>
<body style="margin:0;background:#000">
<div id="player" style="width:{W}px;height:{H}px;position:relative">
<video id="v" src="v.webm" muted loop autoplay playsinline style="width:100%;height:100%"></video></div>
<script>
const store = {{ enabled: true, autoDetect: false, config: {json.dumps(CFG)} }};
window.chrome = {{ storage: {{
  sync: {{ get: (d, cb) => cb({{ ...d, ...store }}), set: (o, cb) => cb && cb() }},
  local: {{ get: (d, cb) => cb(d), set: (o, cb) => cb && cb() }},
  onChanged: {{ addListener() {{}} }} }} }};
</script>
<script src="squish.js"></script><script src="starvation.js"></script><script src="content.js"></script></body></html>"""


def serve(tmp):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: Quiet(*a, directory=tmp))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def new_dir():
    tmp = tempfile.mkdtemp()
    open(os.path.join(tmp, "index.html"), "w").write(page_html())
    for n in ("squish.js", "starvation.js", "content.js"):
        os.symlink(os.path.join(EXT, n), os.path.join(tmp, n))
    return tmp


def main():
    from playwright.sync_api import sync_playwright
    src = picture()
    ok = True
    args = ["--autoplay-policy=no-user-gesture-required", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
    with sync_playwright() as p:
        b = p.chromium.launch(args=args)
        for code in range(6):
            tmp = new_dir()
            fd = os.path.join(tmp, "fr")
            os.makedirs(fd)
            frame = sm.render(src, code, cfg=CFG)
            for i in range(30):
                Image.fromarray(frame).save(os.path.join(fd, f"{i:03d}.png"))
            encode(fd, os.path.join(tmp, "v.webm"))
            srv = serve(tmp)
            page = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            page.goto(f"http://127.0.0.1:{srv.server_port}/index.html")
            page.wait_for_function("document.querySelector('canvas[data-dbpo-unsquish]') !== null", timeout=15000)
            page.wait_for_timeout(800)
            d = page.evaluate("""() => { const c = document.querySelector('canvas[data-dbpo-unsquish]'); const gl = c.getContext('webgl');
                const px = new Uint8Array(c.width * c.height * 4); gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
                return { w: c.width, h: c.height, px: Array.from(px), level: document.documentElement.dataset.dbpoLevel }; }""")
            page.close()
            srv.shutdown()
            out = np.array(d["px"], np.uint8).reshape(d["h"], d["w"], 4)[::-1, :, :3].astype(float)
            ref = sm.view(frame, code, cfg=CFG).astype(float)
            mask = np.ones((H, W), bool)
            mx, my = sm.marker_pos(W, H)
            mask[my - 2:my + 18, mx - 2:] = False  # the marker strip is not part of the picture
            err = np.abs(out - ref)[mask].mean()
            lvl_ok = d["level"] == str(code)
            good = err <= 1.0 and lvl_ok and (d["w"], d["h"]) == (W, H)
            ok &= good
            print(f"level {code}: decoded level {d['level']}, mean error vs reference {err:.2f}/255 {'PASS' if good else 'FAIL'}")

        # Switching clip: 0 -> 2 -> 3 -> 5 -> 0, 20 frames each at 30 fps, looped.
        tmp = new_dir()
        fd = os.path.join(tmp, "fr")
        os.makedirs(fd)
        seq = [0] * 20 + [2] * 20 + [3] * 20 + [5] * 20
        for i, c in enumerate(seq):
            Image.fromarray(sm.render(np.roll(src, i * 2, axis=1), c, cfg=CFG)).save(os.path.join(fd, f"{i:03d}.png"))
        encode(fd, os.path.join(tmp, "v.webm"))
        srv = serve(tmp)
        page = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        page.goto(f"http://127.0.0.1:{srv.server_port}/index.html")
        page.wait_for_function("document.querySelector('canvas[data-dbpo-unsquish]') !== null", timeout=15000)
        page.evaluate("() => { window.__lv = []; const d = document.documentElement; new MutationObserver(() => window.__lv.push(d.dataset.dbpoLevel)).observe(d, { attributes: true, attributeFilter: ['data-dbpo-level'] }); }")
        page.wait_for_timeout(6000)
        lv = page.evaluate("() => window.__lv")
        page.close()
        srv.shutdown()
        b.close()
    changes = [lv[0]] if lv else []
    for a in lv[1:]:
        if a != changes[-1]:
            changes.append(a)
    allowed = {("0", "2"), ("2", "3"), ("3", "5"), ("5", "0")}
    path_ok = len(changes) >= 4 and all((a, c) in allowed for a, c in zip(changes, changes[1:])) and {"0", "2", "3", "5"} <= set(changes)
    ok &= path_ok
    print(f"switching clip: level changes {' '.join(changes)} -> {'PASS' if path_ok else 'FAIL'}")
    print("starvation e2e:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
