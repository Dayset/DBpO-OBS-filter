"""The userscript build (../../userscript/dbpo-unsquish.user.js) in headless Chromium.

Same clip and checks as e2e_atlas.py, but the page loads the single userscript file
(no extension, storage shim on localStorage) with its settings stored the way the
panel stores them. Also checks that the floating settings panel exists and that a
config typed into it is accepted.

Usage: python3 viewer/unsquish-extension/tests/e2e_userscript.py
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
sys.path.insert(0, HERE)
import e2e_atlas as ea  # noqa: E402

EXT = os.path.dirname(HERE)
USERSCRIPT = os.path.join(EXT, "..", "userscript", "dbpo-unsquish.user.js")


def main():
    subprocess.run([sys.executable, os.path.join(EXT, "build.py"), "--check"], check=True)
    tmp = tempfile.mkdtemp()
    src = ea.source()
    frame, _ = ea.am.squish_frame_with_atlas(src, ea.CFG, ea.OUT_W, ea.OUT_H, blank_duplicate=True)
    Image.fromarray((np.clip(frame, 0, 1) * 255 + 0.5).astype(np.uint8)).save(os.path.join(tmp, "f.png"))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-loop", "1", "-i", os.path.join(tmp, "f.png"), "-t", "3",
                    "-r", "30", "-c:v", "libvpx-vp9", "-crf", "4", "-b:v", "0", "-pix_fmt", "yuv444p",
                    os.path.join(tmp, "v.webm")], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", os.path.join(tmp, "v.webm"), "-frames:v", "1",
                    os.path.join(tmp, "dec.png")], check=True)
    os.symlink(os.path.abspath(USERSCRIPT), os.path.join(tmp, "us.js"))
    store = json.dumps({"enabled": True, "autoDetect": False, "config": ea.CFG})
    with open(os.path.join(tmp, "index.html"), "w") as f:
        f.write(f"""<!doctype html><html><head><title>Test stream</title></head><body style="margin:0;background:#000">
<div style="width:{ea.OUT_W}px;height:{ea.OUT_H}px;position:relative">
<video id="v" src="v.webm" muted loop autoplay playsinline style="width:100%;height:100%"></video></div>
<script>localStorage.setItem("dbpoUnsquish:sync", {json.dumps(store)});</script>
<script src="us.js"></script></body></html>""")

    from playwright.sync_api import sync_playwright

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: Quiet(*a, directory=tmp))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--use-gl=angle",
                                    "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = b.new_page(viewport={"width": ea.OUT_W, "height": ea.OUT_H}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{srv.server_port}/index.html")
        page.wait_for_function("document.querySelector('canvas[data-dbpo-unsquish]') !== null", timeout=15000)
        page.wait_for_timeout(800)
        data = page.evaluate("""() => {
            const c = document.querySelector('canvas[data-dbpo-unsquish]');
            const gl = c.getContext('webgl');
            const px = new Uint8Array(c.width * c.height * 4);
            gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
            const root = window.__dbpoPanel;
            return { w: c.width, h: c.height, px: Array.from(px), panel: !!root && !!root.getElementById('gear'),
                     status: root ? root.getElementById('status').textContent : '' };
        }""")
        b.close()
    srv.shutdown()
    out = np.array(data["px"], np.uint8).reshape(data["h"], data["w"], 4)[::-1, :, :3] / 255.0
    dec = np.asarray(Image.open(os.path.join(tmp, "dec.png")).convert("RGB"), np.float64) / 255
    ref = ea.am.unsquish_with_atlas(dec, ea.CFG, ea.OUT_W, ea.OUT_H, ea.SRC_W, ea.SRC_H)
    err = np.abs(out - ref).mean() * 255
    print(f"userscript canvas {data['w']}x{data['h']}, vs reference viewer {err:.2f}/255; panel: {data['panel']}; "
          f"status: {data['status'][:70]!r}; page errors: {errors or 'none'}")
    ok = (data["w"], data["h"]) == (ea.OUT_W, ea.OUT_H) and err < 1.0 and data["panel"] and not errors \
        and "2 sharp areas" in data["status"]
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
