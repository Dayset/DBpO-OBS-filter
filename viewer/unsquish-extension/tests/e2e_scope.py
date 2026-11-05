"""A pasted config applies only on the page it was pasted for; elsewhere the extension idles.

Usage: python3 viewer/unsquish-extension/tests/e2e_scope.py
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
PAGE = """<!doctype html><html><head><title>Plain title</title></head><body style="margin:0">
<div style="width:320px;height:180px"><video src="clip.webm" muted loop autoplay playsinline style="width:100%;height:100%"></video></div>
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
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=2",
                    "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", os.path.join(tmp, "clip.webm")], check=True)
    cases = {  # page file -> (store, canvas expected)
        "here.html": ({"enabled": True, "autoDetect": False, "config": CFG, "configPage": "127.0.0.1/here.html"}, True),
        "elsewhere.html": ({"enabled": True, "autoDetect": False, "config": CFG, "configPage": "www.twitch.tv/other"}, False),
        "legacy.html": ({"enabled": True, "autoDetect": False, "config": CFG}, True),
    }
    for name, (store, _) in cases.items():
        open(os.path.join(tmp, name), "w").write(PAGE.replace("@@STORE@@", json.dumps(store)))

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
        for name, (_, want) in cases.items():
            page = b.new_page(viewport={"width": 320, "height": 180})
            page.goto(f"http://127.0.0.1:{srv.server_port}/{name}")
            page.wait_for_timeout(1500)
            has = page.evaluate("document.querySelector('canvas[data-dbpo-unsquish]') !== null")
            good = has == want
            ok = ok and good
            print(f"{name}: canvas {'drawn' if has else 'absent'} (expected {'drawn' if want else 'absent'}) {'ok' if good else 'WRONG'}")
            page.close()
        b.close()
    srv.shutdown()
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
