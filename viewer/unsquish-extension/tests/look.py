"""Viewer look (NVIDIA Freestyle-style Details + Sharpen+) in headless Chromium.

Plays a video through content.js (chrome.storage stubbed), reads the canvas
with the look off, turns the look on live and reads it again, and checks the
result against a numpy model of the same passes (bilinear sampling, 8-bit
framebuffers, half-resolution blur). Cases: the streamer's look carried in the
FSQ1 string on a squished stream, the viewer's own look, the look turned off
by the viewer, and a plain (not squished) video.

Usage: python3 viewer/unsquish-extension/tests/look.py
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
W, H = 640, 360
LUMA = np.array([0.299, 0.587, 0.114])


def sample(img, u, v):
    """Bilinear, clamp-to-edge texture lookup; img is top-down (row 0 = v 0)."""
    h, w = img.shape[:2]
    x = np.clip(u * w - 0.5, 0, w - 1)
    y = np.clip(v * h - 0.5, 0, h - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    return ((img[y0, x0] * (1 - fx) + img[y0, x1] * fx) * (1 - fy) + (img[y1, x0] * (1 - fx) + img[y1, x1] * fx) * fy)


def q8(img):
    return np.round(np.clip(img, 0, 1) * 255) / 255


def blur(img, ow, oh, step):
    v, u = (np.mgrid[0:oh, 0:ow] + 0.5) / np.array([oh, ow])[:, None, None]
    norm = -1.35914091423 / 225.0
    acc = sample(img, u, v) * 0.5
    div = 0.5
    for i in range(1, 16):
        o, wt = i * 2 - 0.5, np.exp(i * i * norm)
        acc += (sample(img, u + step[0] * o, v + step[1] * o) + sample(img, u - step[0] * o, v - step[1] * o)) * wt
        div += wt
    return q8(acc / (2 * div))


def apply_look(img, look):
    """img: HxWx3 in 0..1, top-down (what the unsquish pass drew). Same passes as content.js."""
    sh, cl, hd, bl, sp = (look[k] for k in ("sharpen", "clarity", "hdr", "bloom", "sharpenPlus"))
    h, w = img.shape[:2]
    v, u = (np.mgrid[0:h, 0:w] + 0.5) / np.array([h, w])[:, None, None]
    out = img
    if sh or cl or hd or bl:
        hw, hh = (w + 1) // 2, (h + 1) // 2
        s = max(h / 1080, 0.25)
        big = blur(blur(img, hw, hh, (s / w, 0)), hw, hh, (0, s / h))
        big = sample(big, u, v)
        tap = lambda x, y: sample(img, u + x / w, v + y / h)  # noqa: E731
        small = ((tap(0.5, 1.5) + tap(1.5, -0.5) + tap(-0.5, -1.5) + tap(-1.5, 0.5)) * 1.5 + tap(2.5, 1.5)
                 + tap(1.5, -2.5) + tap(-2.5, -1.5) + tap(-1.5, 2.5)) / 10
        color = img.copy()
        a, b = color @ LUMA, big @ LUMA
        ia = (1 / np.maximum(a, 1e-6))[..., None]
        sa = np.sqrt(a)
        hdr = sa * np.where(b > 0.5, 2 * sa * b - 2 * b + 1, sa * (2 * a * b - a - 2 * b + 2))
        color = color * ia * (a + (hdr - a) * hd)[..., None]
        lim = 0.25 + 0.35 * sh
        sv = np.clip((color - small) @ LUMA, -lim, lim)
        color = color * ia * (a + sv * sh)[..., None]
        c = 0.5 + a - b
        c = np.where(a > b, 2 * (1 - c) + (2 * c - 1) / np.sqrt(np.maximum(a, 1e-6)), 2 * c + a * (1 - 2 * c))
        color = color * (1 + (c - 1) * cl)[..., None]
        color = 1 - (1 - color) * (1 - big * bl)
        out = q8(color)
    if sp:
        t = lambda x, y: sample(out, u + x / w, v + y / h)  # noqa: E731
        a, b, c, d, e, f, g, hh_, i = (t(-1, -1), t(0, -1), t(1, -1), t(-1, 0), out, t(1, 0), t(-1, 1), t(0, 1), t(1, 1))
        mn = np.minimum.reduce([d, e, f, b, hh_])
        mn = mn + np.minimum(mn, np.minimum.reduce([a, c, g, i]))
        mx = np.maximum.reduce([d, e, f, b, hh_])
        mx = mx + np.maximum(mx, np.maximum.reduce([a, c, g, i]))
        amp = np.sqrt(np.clip(np.minimum(mn, 2 - mx) / np.maximum(mx, 1e-5), 0, 1))
        wt = amp * (-1 / (8 + (5 - 8) * sp))
        out = q8(((b + d + f + hh_) * wt + e) / (1 + 4 * wt))
    return out


def picture():
    y, x = np.mgrid[0:H, 0:W]
    img = np.zeros((H, W, 3))
    img[..., 0] = 0.15 + 0.7 * x / W
    img[..., 1] = 0.1 + 0.6 * y / H
    img[..., 2] = 0.5 + 0.35 * np.sin(x / 7.0) * np.sin(y / 9.0)
    img[(np.abs(x - 320) < 40) & (np.abs(y - 180) < 30)] = (0.95, 0.95, 0.9)  # bright patch (bloom)
    img[(np.abs(x - 120) < 30) & (np.abs(y - 90) < 20)] = (0.05, 0.05, 0.08)  # dark patch (clarity)
    return img


STREAMER = {"sharpen": 0.6, "clarity": 0.5, "hdr": 0.4, "bloom": 0.3, "sharpenPlus": 0.0}
MINE = {"sharpen": 0.3, "clarity": 0.0, "hdr": 0.0, "bloom": 0.0, "sharpenPlus": 0.8}
CFG = "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.7778,ls=0.6,lc=0.5,lh=0.4,lb=0.3"


def main():
    tmp = tempfile.mkdtemp()
    Image.fromarray((picture() * 255 + 0.5).astype(np.uint8)).save(os.path.join(tmp, "f.png"))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-loop", "1", "-i", os.path.join(tmp, "f.png"), "-t", "3",
                    "-r", "30", "-c:v", "libvpx-vp9", "-crf", "4", "-b:v", "0", "-pix_fmt", "yuv444p",
                    os.path.join(tmp, "v.webm")], check=True)
    with open(os.path.join(tmp, "index.html"), "w") as f:
        f.write(f"""<!doctype html><html><head><title>Look test</title></head>
<body style="margin:0;background:#000">
<div id="player" style="width:{W}px;height:{H}px;position:relative">
<video id="v" src="v.webm" muted loop autoplay playsinline style="width:100%;height:100%"></video></div>
<script>
const store = {{ enabled: true, autoDetect: false, config: {json.dumps(CFG)} }};
const listeners = [];
window.setSettings = (o) => {{ const ch = {{}}; for (const k in o) {{ ch[k] = {{ newValue: o[k] }}; store[k] = o[k]; }} listeners.forEach((l) => l(ch)); }};
window.chrome = {{ storage: {{
  sync: {{ get: (d, cb) => cb({{ ...d, ...store }}), set: (o, cb) => cb && cb() }},
  local: {{ get: (d, cb) => cb(d), set: (o, cb) => cb && cb() }},
  onChanged: {{ addListener(l) {{ listeners.push(l); }} }} }} }};
</script>
<script src="squish.js"></script><script src="content.js"></script></body></html>""")
    for n in ("squish.js", "content.js"):
        os.symlink(os.path.join(EXT, n), os.path.join(tmp, n))

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
        page = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        logs = []
        page.on("console", lambda m: logs.append(m.text))
        page.goto(f"http://127.0.0.1:{srv.server_port}/index.html")
        page.wait_for_function("document.querySelector('canvas[data-dbpo-unsquish]') !== null", timeout=15000)

        def grab(settings):
            page.evaluate(f"setSettings({json.dumps(settings)})")
            page.wait_for_timeout(700)
            d = page.evaluate("""() => {
                const c = document.querySelector('canvas[data-dbpo-unsquish]');
                if (!c) return null;
                const gl = c.getContext('webgl'), px = new Uint8Array(c.width * c.height * 4);
                gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
                return { w: c.width, h: c.height, px: Array.from(px), look: document.documentElement.dataset.dbpoLook,
                         tag: document.documentElement.dataset.dbpoUnsquish };
            }""")
            if d is None:
                return None, None
            img = np.array(d["px"], np.uint8).reshape(d["h"], d["w"], 4)[::-1, :, :3] / 255.0
            return img, d

        def check(name, base, got, look, info):
            nonlocal ok
            ref = apply_look(base, look)
            err = np.abs(got - ref) * 255
            changed = np.abs(got - base).mean() * 255
            good = err.mean() < 1.0 and np.percentile(err, 99.9) < 12 and changed > 0.3
            print(f"{'PASS' if good else 'FAIL'} {name}: vs numpy model mean {err.mean():.3f}/255, 99.9% {np.percentile(err, 99.9):.1f}/255;"
                  f" change from no look {changed:.2f}/255; tag {info['tag'][:24]}, look {info['look']}")
            Image.fromarray((got * 255).astype(np.uint8)).save(os.path.join(tmp, f"{name}.png"))
            ok = ok and good

        base, _ = grab({"lookMode": "off"})
        img, info = grab({"lookMode": "streamer"})
        check("streamer-look", base, img, STREAMER, info)
        img, info = grab({"lookMode": "mine", "myLook": MINE})
        check("my-look", base, img, MINE, info)
        img, info = grab({"lookMode": "off"})
        off_ok = np.abs(img - base).max() == 0 and info["look"] == ""
        print(f"{'PASS' if off_ok else 'FAIL'} look off: identical to the plain unsquish")
        ok = ok and off_ok
        # Plain video: no config, viewer's look on normal videos.
        none, _ = grab({"config": "", "lookMode": "mine", "plainVideos": False})
        none_ok = none is None
        print(f"{'PASS' if none_ok else 'FAIL'} plain video untouched while 'normal videos' is off")
        all_on = {"sharpen": 0.5, "clarity": 0.5, "hdr": 0.5, "bloom": 0.2, "sharpenPlus": 0.5}
        # Reference picture: the same video through an identity config with no look.
        src, _ = grab({"config": "FSQ1:rx=1,ry=1,cx=0.5,cy=0.5,fx=0.5,fy=0.5,px=1,py=1,ar=1.7778", "lookMode": "off"})
        grab({"config": "", "lookMode": "mine"})
        img, info = grab({"plainVideos": True, "myLook": all_on})
        if img is None:
            print("FAIL plain video: no canvas")
            ok = False
        else:
            check("plain-video", src, img, all_on, info)
            ok = ok and info["tag"] == "plain"
        ok = ok and none_ok
        b.close()
    srv.shutdown()
    for line in logs:
        if "GPU stall" not in line:
            print("console:", line)
    print("PASS" if ok else "FAIL", f"(images in {tmp})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
