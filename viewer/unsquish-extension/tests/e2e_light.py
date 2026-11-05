"""Light quality: fewer canvas pixels on a high-density screen, still a correct picture.

Usage: python3 viewer/unsquish-extension/tests/e2e_light.py
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.dirname(HERE)
CFG = "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778"
PAGE = """<!doctype html><html><head><title>t</title></head><body style="margin:0">
<div style="width:640px;height:360px"><video src="clip.webm" muted loop autoplay playsinline style="width:100%;height:100%"></video></div>
<script>
const store = @@STORE@@;
window.chrome = { storage: {
  sync: { get: (d, cb) => cb({ ...d, ...store }), set: (o, cb) => cb && cb() },
  local: { get: (d, cb) => cb(d), set: (o, cb) => cb && cb() },
  onChanged: { addListener() {} } } };
</script><script src="squish.js"></script><script src="content.js"></script></body></html>"""


def main():
    tmp = tempfile.mkdtemp()
    for n in ("squish.js", "content.js"):
        os.symlink(os.path.join(EXT, n), os.path.join(tmp, n))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2",
                    "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", os.path.join(tmp, "clip.webm")], check=True)
    for q in ("full", "light"):
        store = {"enabled": True, "autoDetect": False, "config": CFG, "quality": q}
        open(os.path.join(tmp, f"{q}.html"), "w").write(PAGE.replace("@@STORE@@", json.dumps(store)))

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: Quiet(*a, directory=tmp))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    from playwright.sync_api import sync_playwright
    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--use-gl=angle",
                                    "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        for q in ("full", "light"):
            page = b.new_page(viewport={"width": 640, "height": 360}, device_scale_factor=3)
            page.goto(f"http://127.0.0.1:{srv.server_port}/{q}.html")
            page.wait_for_function("document.querySelector('canvas[data-dbpo-unsquish]') !== null", timeout=15000)
            page.wait_for_timeout(800)
            out[q] = page.evaluate("""() => { const c = document.querySelector('canvas[data-dbpo-unsquish]');
                const gl = c.getContext('webgl'); const px = new Uint8Array(c.width * c.height * 4);
                gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
                let s = 0, s2 = 0; for (let i = 0; i < px.length; i += 4) { s += px[i + 1]; s2 += px[i + 1] * px[i + 1]; }
                const n = px.length / 4; return { w: c.width, h: c.height, mean: s / n, sd: Math.sqrt(s2 / n - (s / n) ** 2) }; }""")
            page.close()
        b.close()
    srv.shutdown()
    f, l = out["full"], out["light"]
    print(f"full canvas {f['w']}x{f['h']}, light canvas {l['w']}x{l['h']}; picture mean {f['mean']:.1f} vs {l['mean']:.1f}")
    ok = l["w"] * l["h"] < 0.4 * f["w"] * f["h"] and l["sd"] > 10 and abs(l["mean"] - f["mean"]) < 12
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
