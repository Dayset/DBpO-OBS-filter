"""End-to-end check of sharp regions (atlas) in the extension, headless Chromium.

Builds a streamed frame with the Python reference (squished picture in a box +
atlas strip), encodes it as VP9, plays it with content.js injected and compares
the WebGL canvas with the reference viewer (unsquish_with_atlas) run on the same
decoded frame. Also checks that a region really is sharper than the plain
squish would give, and that the border of every region has no seam.

Usage: python3 viewer/unsquish-extension/tests/e2e_atlas.py
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
import atlas_math as am  # noqa: E402

SRC_W, SRC_H = 1280, 720  # game picture
OUT_W, OUT_H = 640, 360  # stream, played at the same size
CFG = ("FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778,ox=0,oy=0,bw=0.800,bh=1,"
       "gx=0.800,gy=0.850,gz=1.000,gv=1.000,gs=1.000,hx=0.010,hy=0.610,hz=0.210,hv=0.980,hs=1.000")
PLAIN = ("FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778,ox=0,oy=0,bw=0.800,bh=1")


def source():
    """Smooth picture with minimap-like fine detail inside both regions."""
    y, x = np.mgrid[0:SRC_H, 0:SRC_W]
    img = np.zeros((SRC_H, SRC_W, 3))
    img[..., 0] = x / SRC_W
    img[..., 1] = y / SRC_H
    img[..., 2] = 0.5 + 0.3 * np.sin(x / 25.0) * np.sin(y / 31.0)
    for x0, y0, x1, y1 in ((0.8, 0.85, 1.0, 1.0), (0.01, 0.61, 0.21, 0.98)):
        m = (x >= x0 * SRC_W) & (x < x1 * SRC_W) & (y >= y0 * SRC_H) & (y < y1 * SRC_H)
        lines = 0.5 + 0.5 * np.sin(x * 2 * np.pi / 14.0) * np.sin(y * 2 * np.pi / 18.0)  # fine, smooth: needs ~full resolution
        img[m] = (0.15 + 0.7 * lines[m])[:, None] * np.array([1.0, 0.9, 0.6])
    return img


def play(cfg, frame_u8, tmp, name):
    Image.fromarray(frame_u8).save(os.path.join(tmp, f"{name}.png"))
    webm = os.path.join(tmp, f"{name}.webm")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-loop", "1", "-i", os.path.join(tmp, f"{name}.png"),
                    "-t", "3", "-r", "30", "-c:v", "libvpx-vp9", "-crf", "4", "-b:v", "0", "-pix_fmt", "yuv444p",
                    webm], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", webm, "-frames:v", "1",
                    os.path.join(tmp, f"{name}_dec.png")], check=True)
    with open(os.path.join(tmp, f"{name}.html"), "w") as f:
        f.write(f"""<!doctype html><html><head><title>Test stream</title></head>
<body style="margin:0;background:#000">
<div id="player" style="width:{OUT_W}px;height:{OUT_H}px;position:relative">
<video id="v" src="{name}.webm" muted loop autoplay playsinline style="width:100%;height:100%"></video></div>
<script>
const store = {{ enabled: true, autoDetect: false, config: {json.dumps(cfg)} }};
window.chrome = {{ storage: {{
  sync: {{ get: (d, cb) => cb({{ ...d, ...store }}), set: (o, cb) => cb && cb() }},
  local: {{ get: (d, cb) => cb(d), set: (o, cb) => cb && cb() }},
  onChanged: {{ addListener() {{}} }} }} }};
</script>
<script src="squish.js"></script><script src="content.js"></script></body></html>""")
    from playwright.sync_api import sync_playwright

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: Quiet(*a, directory=tmp))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--use-gl=angle",
                                    "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = b.new_page(viewport={"width": OUT_W, "height": OUT_H}, device_scale_factor=1)
        logs = []
        page.on("console", lambda m: logs.append(m.text))
        page.goto(f"http://127.0.0.1:{srv.server_port}/{name}.html")
        page.wait_for_function("document.querySelector('canvas[data-dbpo-unsquish]') !== null", timeout=15000)
        page.wait_for_timeout(800)
        data = page.evaluate("""() => {
            const c = document.querySelector('canvas[data-dbpo-unsquish]');
            const gl = c.getContext('webgl');
            const px = new Uint8Array(c.width * c.height * 4);
            gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
            return { w: c.width, h: c.height, px: Array.from(px) };
        }""")
        b.close()
    srv.shutdown()
    for line in logs:
        print("console:", line)
    out = np.array(data["px"], np.uint8).reshape(data["h"], data["w"], 4)[::-1, :, :3] / 255.0
    dec = np.asarray(Image.open(os.path.join(tmp, f"{name}_dec.png")).convert("RGB"), np.float64) / 255
    return out, dec, (data["w"], data["h"])


def main():
    tmp = tempfile.mkdtemp()
    for n in ("squish.js", "content.js"):
        os.symlink(os.path.join(EXT, n), os.path.join(tmp, n))
    src = source()
    regions = am.Atlas.parse(CFG).regions

    frame, slots = am.squish_frame_with_atlas(src, CFG, OUT_W, OUT_H, blank_duplicate=True)
    out, dec, size = play(CFG, (np.clip(frame, 0, 1) * 255 + 0.5).astype(np.uint8), tmp, "atlas")
    ref = am.unsquish_with_atlas(dec, CFG, OUT_W, OUT_H, SRC_W, SRC_H)

    pframe, _ = am.squish_frame_with_atlas(src, PLAIN, OUT_W, OUT_H, blank_duplicate=False)
    pout, _, _ = play(PLAIN, (np.clip(pframe, 0, 1) * 255 + 0.5).astype(np.uint8), tmp, "plain")

    # What an ideal viewer picture at the played size looks like: the source, area-averaged down.
    ideal = np.asarray(Image.fromarray((src * 255 + 0.5).astype(np.uint8)).resize((OUT_W, OUT_H), Image.BOX),
                       np.float64) / 255

    ok = size == (OUT_W, OUT_H)
    err_all = np.abs(out - ref).mean() * 255
    print(f"canvas {size[0]}x{size[1]}; whole frame vs reference viewer: mean error {err_all:.2f}/255")
    ok = ok and err_all < 1.0
    for i, r in enumerate(regions):
        x0, x1 = int(round(r.x0 * OUT_W)), int(round(r.x1 * OUT_W))
        y0, y1 = int(round(r.y0 * OUT_H)), int(round(r.y1 * OUT_H))
        inner = (slice(y0 + 2, y1 - 2), slice(x0 + 2, x1 - 2))
        e_ref = np.abs(out[inner] - ref[inner]).mean() * 255
        e_atlas = np.abs(out[inner] - ideal[inner]).mean() * 255
        e_plain = np.abs(pout[inner] - ideal[inner]).mean() * 255
        # Border ring (2 px each side of the region edge): no seam against the reference.
        ring = np.zeros((OUT_H, OUT_W), bool)
        ring[max(y0 - 2, 0):y1 + 2, max(x0 - 2, 0):x1 + 2] = True
        ring[y0 + 2:y1 - 2, x0 + 2:x1 - 2] = False
        e_ring = np.abs(out - ref)[ring].max() * 255
        print(f"region {i + 1}: vs reference {e_ref:.2f}/255, vs ideal {e_atlas:.1f}/255 "
              f"(plain squish {e_plain:.1f}/255), worst border pixel vs reference {e_ring:.1f}/255")
        ok = ok and e_ref < 1.5 and e_atlas < 0.75 * e_plain and e_ring < 8
    Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8)).save(os.path.join(tmp, "unsquished.png"))
    print("PASS" if ok else "FAIL", f"(images in {tmp})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
