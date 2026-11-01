"""End-to-end check of the extension in headless Chromium.

Squishes a test picture with the reference math (exact area filter), encodes it
as a lossless-ish VP9 webm, plays it on a local page with content.js injected
(chrome.storage stubbed, config pasted), reads the WebGL canvas back and
compares it with the original picture: the sharp center must match closely and
the whole frame must be the right geometry (no offsets, no flips).

Usage: python3 viewer/unsquish-extension/tests/e2e.py
"""
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(EXT, "..", "..", "research", "foveation"))
from squish_math import Axis, Squish  # noqa: E402
from squish_profiles import bilinear_matrix  # noqa: E402

W, H = 640, 360
LANDMARKS = ((0.1, 0.1), (0.5, 0.45), (0.9, 0.85))
CFG = Squish(Axis(r=0.75, c=0.15, p=1.0), Axis(r=0.7, c=0.2, f=0.45, p=0.75), ar=W / H)


def warp2(rows, img, cols):
    """Separable resample: rows (out_h x in_h) @ img @ cols.T (out_w x in_w)."""
    return np.einsum("vx,uxc->uvc", cols, np.einsum("uy,yxc->uxc", rows, img, optimize=True), optimize=True)


def area_matrix(axis, n_src, n_out):
    edges = axis.inverse(np.arange(n_out + 1) / n_out) * n_src
    A = np.zeros((n_out, n_src))
    for u in range(n_out):
        a, b = edges[u], edges[u + 1]
        for i in range(max(int(np.floor(a)), 0), min(int(np.ceil(b)), n_src)):
            A[u, i] = max(0.0, min(b, i + 1) - max(a, i))
        A[u] /= A[u].sum()
    return A


def test_picture():
    y, x = np.mgrid[0:H, 0:W]
    img = np.zeros((H, W, 3))
    img[..., 0] = x / W
    img[..., 1] = y / H
    img[..., 2] = 0.5 + 0.4 * np.sin(x / 9.0) * np.sin(y / 11.0)  # smooth texture
    for cx, cy in LANDMARKS:
        m = (np.abs(x / W - cx) < 0.03) & (np.abs(y / H - cy) < 0.04)
        img[m] = (1, 1, 1)
    return img


def main():
    tmp = tempfile.mkdtemp()
    src = test_picture()
    mw, mh = int(round(W * CFG.x.r / 2)) * 2, int(round(H * CFG.y.r / 2)) * 2
    CFG.x.r, CFG.y.r = mw / W, mh / H
    sq = warp2(area_matrix(CFG.y, H, mh), src, area_matrix(CFG.x, W, mw))
    frame = (np.clip(sq, 0, 1) * 255 + 0.5).astype(np.uint8)
    Image.fromarray(frame).save(os.path.join(tmp, "f.png"))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-loop", "1", "-i", os.path.join(tmp, "f.png"), "-t", "3",
                    "-r", "30", "-c:v", "libvpx-vp9", "-crf", "4", "-b:v", "0", "-pix_fmt", "yuv444p",
                    os.path.join(tmp, "v.webm")], check=True)
    cfg = CFG.to_string()
    with open(os.path.join(tmp, "index.html"), "w") as f:
        f.write(f"""<!doctype html><html><head><title>Test stream</title></head>
<body style="margin:0;background:#000">
<div id="player" style="width:{W}px;height:{H}px;position:relative">
<video id="v" src="v.webm" muted loop autoplay playsinline style="width:100%;height:100%"></video></div>
<script>
const store = {{ enabled: true, autoDetect: false, config: {json.dumps(cfg)} }};
window.chrome = {{ storage: {{
  sync: {{ get: (d, cb) => cb({{ ...d, ...store }}), set: (o, cb) => cb && cb() }},
  local: {{ get: (d, cb) => cb(d), set: (o, cb) => cb && cb() }},
  onChanged: {{ addListener() {{}} }} }} }};
</script>
<script src="squish.js"></script><script src="content.js"></script></body></html>""")
    for n in ("squish.js", "content.js"):
        os.symlink(os.path.join(EXT, n), os.path.join(tmp, n))

    from playwright.sync_api import sync_playwright
    import http.server
    import threading
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = lambda *a: Quiet(*a, directory=tmp)  # noqa: E731
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--use-gl=angle",
                                    "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        logs = []
        page.on("console", lambda m: logs.append(m.text))
        page.goto(f"http://127.0.0.1:{srv.server_port}/index.html")
        page.wait_for_function("document.querySelector('canvas[data-dbpo-unsquish]') !== null", timeout=15000)
        page.wait_for_timeout(800)
        data = page.evaluate("""() => {
            const c = document.querySelector('canvas[data-dbpo-unsquish]');
            const gl = c.getContext('webgl');
            const px = new Uint8Array(c.width * c.height * 4);
            gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
            return { w: c.width, h: c.height, px: Array.from(px),
                     opacity: document.getElementById('v').style.opacity, tag: document.documentElement.dataset.dbpoUnsquish };
        }""")
        b.close()
    srv.shutdown()
    out = np.array(data["px"], np.uint8).reshape(data["h"], data["w"], 4)[::-1, :, :3] / 255.0  # GL is bottom-up
    print(f"canvas {data['w']}x{data['h']}, video opacity '{data['opacity']}', config tag {data['tag']}")
    for l in logs:
        print("console:", l)
    ok = (data["w"], data["h"]) == (W, H) and data["opacity"] == "0"
    y, x = np.mgrid[0:H, 0:W]
    fov = (np.abs(x / W - CFG.x.f) < CFG.x.c * 0.9) & (np.abs(y / H - CFG.y.f) < CFG.y.c * 0.9)
    err_c = np.abs(out - src)[fov].mean() * 255
    err_all = np.abs(out - src).mean() * 255
    # Geometry: the white landmark boxes must land where they are in the source.
    def centroids(img):
        out = []
        for cx, cy in LANDMARKS:
            win = (np.abs(x / W - cx) < 0.06) & (np.abs(y / H - cy) < 0.08)
            wgt = np.where(win, np.clip(img.min(axis=2) - 0.75, 0, None), 0)
            out.append(((wgt * x).sum() / wgt.sum(), (wgt * y).sum() / wgt.sum()))
        return np.array(out)
    # The reference viewer (bilinear unsquish in numpy) on the same decoded frame.
    dec = np.asarray(Image.open(os.path.join(tmp, "f.png")), np.float64) / 255
    ref = warp2(bilinear_matrix(CFG.y, H, mh), dec, bilinear_matrix(CFG.x, W, mw))
    err_ref = np.abs(out - ref).mean() * 255
    shift = np.abs(centroids(out) - centroids(ref))
    print(f"vs reference unsquish: mean error {err_ref:.2f}/255; shift vs source: "
          + ", ".join(f"({a:.2f}, {b:.2f})" for a, b in np.abs(centroids(out) - centroids(src))))
    print(f"sharp center mean error {err_c:.2f}/255, whole frame {err_all:.2f}/255, landmark shift vs reference (px): "
          + ", ".join(f"({a:.2f}, {b:.2f})" for a, b in shift))
    ok = ok and err_c < 3 and err_all < 8 and err_ref < 2 and shift.max() < 0.25
    Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8)).save(os.path.join(tmp, "unsquished.png"))
    print("PASS" if ok else "FAIL", f"(images in {tmp})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
