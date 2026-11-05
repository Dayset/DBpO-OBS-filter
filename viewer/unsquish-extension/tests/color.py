"""Colour correction in the viewer (headless Chromium, userscript build): the look carried as a
code in the title, applied by the browser, vs the numpy model in research/foveation/color_math.py.

Usage: python3 viewer/unsquish-extension/tests/color.py
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
import color_math as cm  # noqa: E402

W, H = 640, 360
PLAIN = "FSQ1:rx=1,ry=1,cx=0.5,cy=0.5,fx=0.5,fy=0.5,px=1,py=1,ar=1.7778"
CASES = {
    "basics": dict(brightness=0.2, contrast=0.3, gamma=-0.2, highlights=-0.4, shadows=0.5),
    "colour": dict(saturation=0.4, vibrance=0.6, temperature=0.5, tint=-0.3, hue=0.2),
    "effects": dict(bw=0.5, sepia=0.6, night=0.4),
    "vibrance only": dict(vibrance=1.0),
    "extreme": dict(brightness=-1.0, contrast=1.0, saturation=-1.0, hue=-1.0, night=1.0),
}


def picture():
    y, x = np.mgrid[0:H, 0:W]
    hue = x / W * 6.0
    r = np.clip(np.abs(hue - 3) - 1, 0, 1)
    g = np.clip(2 - np.abs(hue - 2), 0, 1)
    b = np.clip(2 - np.abs(hue - 4), 0, 1)
    sat = 0.15 + 0.85 * (y / H)  # dull at the top, vivid at the bottom
    gray = 0.5 + 0.5 * np.sin(x / 40.0)
    rgb = np.stack([r, g, b], axis=-1)
    img = gray[..., None] * (1 - sat[..., None]) + rgb * sat[..., None]
    img[:60] = (x[:60] / W)[..., None]  # grey ramp strip at the top
    return np.clip(img, 0, 1)


def main():
    subprocess.run([sys.executable, os.path.join(EXT, "build.py")], check=True, stdout=subprocess.DEVNULL)
    tmp = tempfile.mkdtemp()
    Image.fromarray((picture() * 255 + 0.5).astype(np.uint8)).save(os.path.join(tmp, "f.png"))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-loop", "1", "-i", os.path.join(tmp, "f.png"), "-t", "3",
                    "-r", "30", "-c:v", "libvpx-vp9", "-crf", "4", "-b:v", "0", "-pix_fmt", "yuv444p",
                    os.path.join(tmp, "v.webm")], check=True)
    os.symlink(os.path.abspath(os.path.join(EXT, "..", "userscript", "dbpo-unsquish.user.js")), os.path.join(tmp, "us.js"))

    from playwright.sync_api import sync_playwright

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: Quiet(*a, directory=tmp))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def read(browser, config):
        store = json.dumps({"enabled": True, "autoDetect": False, "config": config, "lookMode": "streamer"})
        name = f"p{abs(hash(config)) % 10**8}.html"
        with open(os.path.join(tmp, name), "w") as f:
            f.write(f"""<!doctype html><html><body style="margin:0;background:#000">
<div style="width:{W}px;height:{H}px;position:relative"><video id="v" src="v.webm" muted loop autoplay playsinline style="width:100%;height:100%"></video></div>
<script>localStorage.setItem("dbpoUnsquish:sync", {json.dumps(store)});</script><script src="us.js"></script></body></html>""")
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{srv.server_port}/{name}")
        page.wait_for_function("document.querySelector('canvas[data-dbpo-unsquish]') !== null", timeout=15000)
        page.wait_for_timeout(800)
        data = page.evaluate("""() => {
            const c = document.querySelector('canvas[data-dbpo-unsquish]');
            const gl = c.getContext('webgl');
            const px = new Uint8Array(c.width * c.height * 4);
            gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
            return { w: c.width, h: c.height, px: Array.from(px), look: document.documentElement.dataset.dbpoLook };
        }""")
        page.close()
        assert not errors, errors
        return np.array(data["px"], np.uint8).reshape(data["h"], data["w"], 4)[::-1, :, :3] / 255.0, data["look"]

    ok = True
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--use-gl=angle",
                                    "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        base, tag = read(b, PLAIN)
        for name, vals in CASES.items():
            look = {k: 0.0 for k, _ in cm.FIELDS}
            look.update(vals)
            code = cm.pack(look)
            got, tag = read(b, PLAIN + code)
            want = cm.apply_color(base, cm.quantize(look))
            diff = np.abs(got - want)
            moved = np.abs(want - base).mean() * 255
            good = diff.mean() * 255 < 0.6 and diff.max() * 255 < 4 and moved > 0.5
            ok = ok and good
            print(f"{'PASS' if good else 'FAIL'} {name}: code {code} ({len(code)} chars); vs model mean "
                  f"{diff.mean() * 255:.2f}/255, worst {diff.max() * 255:.1f}/255; change from no look {moved:.1f}/255")
        b.close()
    srv.shutdown()
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
