"""Viewer half of spin_compare.py: what a viewer sees on a 2560x1440 screen, from the decoded 6000 kbps streams.

  baseline  the plain 1080p stream, scaled by the browser to the screen
  pipeline  the squished stream through the real DBpO Unsquish extension code (content.js + squish.js, WebGL):
            unsquish + Edge enhance preset "balanced" (Lanczos3, deblock, sharpen, snap, vibrance)
  extra     unsquish only (bilinear), to see what the restoration adds

Usage: python3 spin_compare_viewer.py WORK OUT
Writes OUT/frame_<n>_{original,baseline,pipeline}.png and OUT/extras/frame_<n>_unsquish_only.png
"""
import base64
import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.join(HERE, "..", "..", "viewer", "unsquish-extension")
sys.path.insert(0, HERE)
import spin_compare as sc  # noqa: E402

PAGE = """<!doctype html><html><head><title>t</title></head><body style="margin:0;background:#000">
<div style="width:2560px;height:1440px"><video id="v" src="squish_sel.webm" muted playsinline style="width:100%;height:100%"></video></div>
<script>
let store = @@STORE@@;
window.__listeners = [];
window.chrome = { storage: {
  sync: { get: (d, cb) => cb({ ...d, ...store }), set: (o, cb) => { window.__set(o); cb && cb(); } },
  local: { get: (d, cb) => cb(d), set: (o, cb) => cb && cb() },
  onChanged: { addListener(f) { window.__listeners.push(f); } } } };
window.__set = (o) => { const ch = {}; for (const [k, v] of Object.entries(o)) { ch[k] = { newValue: v }; store[k] = v; } window.__listeners.forEach((f) => f(ch)); };
</script><script src="squish.js"></script><script src="content.js"></script></body></html>"""
PLAIN = """<!doctype html><html><body style="margin:0;background:#000">
<video id="v" src="base_sel.webm" muted playsinline style="width:2560px;height:1440px;object-fit:fill;display:block"></video></body></html>"""

SEEK = """(t) => new Promise((res) => { const v = document.getElementById('v'); v.pause();
  const done = () => setTimeout(() => res(true), 500);
  if (Math.abs(v.currentTime - t) < 1e-3 && v.readyState >= 2) return done();
  v.addEventListener('seeked', done, { once: true }); v.currentTime = t; setTimeout(() => res(false), 15000); })"""
GRAB = """() => document.querySelector('canvas[data-dbpo-unsquish]').toDataURL('image/png')"""


def main():
    work, out = sys.argv[1], sys.argv[2]
    info = json.load(open(os.path.join(work, "frames.json")))
    idx = info["frames"]
    os.makedirs(os.path.join(out, "extras"), exist_ok=True)
    tmp = tempfile.mkdtemp()
    for n in ("squish.js", "content.js"):
        os.symlink(os.path.join(os.path.abspath(EXT), n), os.path.join(tmp, n))
    for n in ("squish_sel.webm", "base_sel.webm"):
        os.symlink(os.path.abspath(os.path.join(work, n)), os.path.join(tmp, n))
    store = {"enabled": True, "autoDetect": False, "config": sc.CFG, "quality": "full", "lookMode": "off"}
    open(os.path.join(tmp, "p.html"), "w").write(PAGE.replace("@@STORE@@", json.dumps(store)))
    open(os.path.join(tmp, "plain.html"), "w").write(PLAIN)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def end_headers(self):
            self.send_header("Accept-Ranges", "bytes")
            super().end_headers()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: Quiet(*a, directory=tmp))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{srv.server_port}"

    # originals (1440p source frames)
    want = {i for i in idx if not os.path.exists(os.path.join(out, f'frame_{i:03d}_original.png'))}
    for i, f in enumerate(sc.read_frames(os.path.join(work, "ref.mkv"), sc.SW, sc.SH) if want else []):
        if i in want:
            Image.fromarray(f).save(os.path.join(out, f"frame_{i:03d}_original.png"), compress_level=3)
        if i > max(want):
            break

    from playwright.sync_api import sync_playwright
    balanced = {"kernel": "lanczos", "area": "periphery", "deblock": 0.5, "sharpen": 0.4, "snap": 0.5, "vibrance": 0.2,
                "posterize": 0, "dither": 0, "grain": 0}
    off = {"kernel": "bilinear", "area": "periphery", "deblock": 0, "sharpen": 0, "snap": 0, "vibrance": 0,
           "posterize": 0, "dither": 0, "grain": 0}
    flags = ["--autoplay-policy=no-user-gesture-required", "--use-gl=angle", "--use-angle=swiftshader",
             "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
    with sync_playwright() as p:
        b = p.chromium.launch(args=flags)
        # baseline: the browser scales the plain 1080p stream
        page = b.new_page(viewport={"width": 2560, "height": 1440})
        page.goto(f"{base_url}/plain.html")
        page.wait_for_function("document.getElementById('v').readyState >= 2", timeout=30000)
        for k, i in enumerate(idx):
            page.evaluate(SEEK, k + 0.5)
            page.wait_for_timeout(700)
            page.screenshot(path=os.path.join(out, f"frame_{i:03d}_baseline.png"), type="png")
            print("baseline", i, flush=True)
        page.close()
        # squished stream through the extension
        page = b.new_page(viewport={"width": 2560, "height": 1440})
        logs = []
        page.on("console", lambda m: logs.append(m.text))
        page.goto(f"{base_url}/p.html")
        page.wait_for_function("document.getElementById('v').readyState >= 2", timeout=30000)
        page.wait_for_function("document.querySelector('canvas[data-dbpo-unsquish]') !== null", timeout=30000)
        for label, enh, path in (("unsquish_only", off, "extras"), ("pipeline", balanced, "")):
            page.evaluate("(e) => window.__set({ enhance: e })", enh)
            last = None
            for k, i in enumerate(idx):
                page.evaluate(SEEK, k + 0.5)
                for attempt in range(6):  # wait until the canvas shows this frame with this setting
                    page.wait_for_timeout(2500)
                    data = page.evaluate(GRAB)
                    h = hashlib.md5(data.encode()).hexdigest()
                    if h != last:
                        break
                last = h
                name = f"frame_{i:03d}_{label}.png" if path else f"frame_{i:03d}_pipeline.png"
                with open(os.path.join(out, path, name), "wb") as f:
                    f.write(base64.b64decode(data.split(",", 1)[1]))
                print(label, i, flush=True)
        bad = [l for l in logs if "unavailable" in l or "cannot read" in l]
        print("extension warnings:", bad or "none")
        b.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
