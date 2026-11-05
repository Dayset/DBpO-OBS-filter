"""Viewer-side edge enhancement: shaders compile, center stays 1:1, periphery changes, debug overlay shows metrics,
split view and benchmark work.

Usage: python3 viewer/unsquish-extension/tests/e2e_enhance.py
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.dirname(HERE)
CFG = "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778"
PAGE = """<!doctype html><html><head><title>t</title></head><body style="margin:0">
<div style="width:640px;height:360px"><video src="clip.webm" muted loop autoplay playsinline style="width:100%;height:100%"></video></div>
<script>
let store = @@STORE@@;
window.__listeners = [];
window.chrome = { storage: {
  sync: { get: (d, cb) => cb({ ...d, ...store }), set: (o, cb) => { window.__set(o); cb && cb(); } },
  local: { get: (d, cb) => cb(d), set: (o, cb) => cb && cb() },
  onChanged: { addListener(f) { window.__listeners.push(f); } } } };
window.__set = (o) => { const ch = {}; for (const [k, v] of Object.entries(o)) { ch[k] = { newValue: v }; store[k] = v; } window.__listeners.forEach((f) => f(ch)); };
</script><script src="squish.js"></script><script src="content.js"></script></body></html>"""

READ = """() => { const c = document.querySelector('canvas[data-dbpo-unsquish]');
  const gl = c.getContext('webgl'); const px = new Uint8Array(c.width * c.height * 4);
  gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px); return { w: c.width, h: c.height, px: Array.from(px) }; }"""


def diff(a, b, cols, rows=(0.0, 1.0)):
    """Mean abs difference (0..255) over a column/row range (fractions of the picture)."""
    w, h = a["w"], a["h"]
    x0, x1 = int(cols[0] * w), int(cols[1] * w)
    tot = n = 0
    for y in range(int(rows[0] * h), int(rows[1] * h), 2):
        for x in range(x0, x1, 2):
            i = (y * w + x) * 4
            tot += sum(abs(a["px"][i + c] - b["px"][i + c]) for c in range(3))
            n += 3
    return tot / n


def main():
    tmp = tempfile.mkdtemp()
    for n in ("squish.js", "content.js"):
        os.symlink(os.path.join(EXT, n), os.path.join(tmp, n))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2",
                    "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", os.path.join(tmp, "clip.webm")], check=True)
    store = {"enabled": True, "autoDetect": False, "config": CFG, "quality": "full", "debug": True}
    open(os.path.join(tmp, "p.html"), "w").write(PAGE.replace("@@STORE@@", json.dumps(store)))

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: Quiet(*a, directory=tmp))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    from playwright.sync_api import sync_playwright
    ok = True
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--use-gl=angle",
                                    "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = b.new_page(viewport={"width": 640, "height": 360})
        logs = []
        page.on("console", lambda m: logs.append(m.text))
        page.goto(f"http://127.0.0.1:{srv.server_port}/p.html")
        page.wait_for_function("document.querySelector('canvas[data-dbpo-unsquish]') !== null", timeout=15000)
        page.wait_for_timeout(800)
        # freeze the video so frames are comparable
        page.evaluate("document.querySelector('video').pause()")
        page.wait_for_timeout(300)
        base = page.evaluate(READ)
        results = {}
        for name, enh in {
            "lanczos": {"kernel": "lanczos"},
            "deblock": {"deblock": 0.8},
            "edge": {"sharpen": 0.8, "snap": 1, "vibrance": 0.5, "posterize": 0.6, "dither": 0.5, "grain": 0.5},
            "all": {"kernel": "lanczos", "deblock": 0.5, "sharpen": 0.5, "snap": 0.8, "vibrance": 0.3},
        }.items():
            page.evaluate("(e) => window.__set({ enhance: e })", enh)
            page.wait_for_timeout(500)
            cur = page.evaluate(READ)
            results[name] = (diff(base, cur, (0.38, 0.62), (0.38, 0.62)), diff(base, cur, (0.0, 0.2)))
            print(f"{name:8} center diff {results[name][0]:.3f}, left periphery diff {results[name][1]:.3f}")
        # centre must stay exactly untouched by every option (except grain/dither which are weight-limited)
        for name, floor in (("lanczos", 0.2), ("deblock", 0.05), ("all", 0.2)):
            ok &= results[name][0] < 0.05 and results[name][1] > floor
        ok &= results["edge"][0] < 0.5 and results["edge"][1] > 1.0
        attr = page.evaluate("document.documentElement.dataset.dbpoEnhance")
        print("data-dbpo-enhance:", attr)
        ok &= bool(attr)
        # debug overlay with metrics
        page.wait_for_timeout(1200)
        text = page.evaluate("(document.querySelector('[data-dbpo-unsquish-debug] pre') || {}).textContent || ''")
        print(text)
        ok &= "GPU" in text and "texture reads" in text and "render" in text
        # split view: left half plain (equals the baseline), right half enhanced
        page.evaluate("window.__set({ compare: 'split', enhance: { kernel: 'lanczos', deblock: 0.5, sharpen: 0.5, snap: 0.8 } })")
        page.wait_for_timeout(600)
        sp = page.evaluate(READ)
        dl, dr = diff(base, sp, (0.02, 0.2)), diff(base, sp, (0.8, 0.98))
        print(f"split: left half vs plain {dl:.3f}, right half vs plain {dr:.3f}")
        ok &= dl < 0.05 and dr > 0.2
        page.evaluate("window.__set({ compare: 'off' })")
        # benchmark
        page.evaluate("document.querySelector('video').play()")
        page.evaluate("[...document.querySelectorAll('[data-dbpo-unsquish-debug] button')].find((b) => b.textContent === 'Benchmark').click()")
        page.wait_for_function("((document.querySelector('[data-dbpo-unsquish-debug] pre') || {}).textContent || '').includes('option\\tGPU ms')",
                               timeout=120000)
        rep = page.evaluate("document.querySelector('[data-dbpo-unsquish-debug] pre').textContent")
        print(rep.split("option\t", 1)[-1] and "option\t" + rep.split("option\t", 1)[-1])
        ok &= "baseline (bilinear)" in rep and "preset crisp" in rep
        bad = [l for l in logs if "unavailable" in l or "rror" in l and "DBpO" in l]
        print("shader warnings:", bad or "none")
        ok &= not bad
        b.close()
    srv.shutdown()
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
