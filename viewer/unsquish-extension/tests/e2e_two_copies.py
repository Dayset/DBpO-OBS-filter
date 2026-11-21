"""Two installed copies of the extension in one Chromium. Only one may own the page at a time, and the copy whose popup
the config was pasted in must take over, so the picture follows the copy the user configured.

Usage (from the repo root): python3 viewer/unsquish-extension/tests/e2e_two_copies.py
"""
import os, sys, shutil, subprocess, tempfile, threading, http.server
from playwright.sync_api import sync_playwright
ext = os.path.abspath("viewer/unsquish-extension")
tmp = tempfile.mkdtemp()
copy = os.path.join(tmp, "copy-b")
shutil.copytree(ext, copy, ignore=shutil.ignore_patterns("tests", "__pycache__"))
subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=480x270:rate=30", "-t", "4",
                "-c:v", "libvpx-vp9", "-b:v", "500k", os.path.join(tmp, "v.webm")], check=True)
open(os.path.join(tmp, "index.html"), "w").write('<!doctype html><title>Stream</title><body style="margin:0"><div style="width:640px;height:360px;position:relative"><video src="v.webm" muted loop autoplay style="width:100%;height:100%;display:block"></video></div>')
class Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: Q(*a, directory=tmp))
threading.Thread(target=srv.serve_forever, daemon=True).start()
CONF_A = "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.7778"
CONF_B = "FSQ1:rx=0.6,ry=0.6,cx=0.1,cy=0.1,fx=0.5,fy=0.5,px=0.5,py=0.5,ar=1.7778"
fails = []
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(tempfile.mkdtemp(), channel="chromium", headless=True, args=[
        f"--disable-extensions-except={ext},{copy}", f"--load-extension={ext},{copy}", "--headless=new",
        "--autoplay-policy=no-user-gesture-required", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    # Each copy's service worker shows up at its own time; collect ids until both are seen.
    ids = set()
    for w in ctx.service_workers: ids.add(w.url.split("/")[2])
    for _ in range(10):
        if len(ids) >= 2: break
        try:
            ids.add(ctx.wait_for_event("serviceworker", timeout=1500).url.split("/")[2])
        except Exception:
            pass
    ids = sorted(ids)
    if len(ids) != 2:
        print("FAIL: expected two extension ids, got", ids); sys.exit(1)
    id_a, id_b = ids  # ids are not known to be A or B; use the popup to map them below
    page = ctx.new_page()
    page.goto(f"http://127.0.0.1:{srv.server_port}/index.html")
    page.wait_for_timeout(1500)
    owner = lambda: page.evaluate("document.documentElement.dataset.dbpoUnsquishOwner || ''")
    overlays = lambda: page.evaluate("document.querySelectorAll('canvas[data-dbpo-unsquish]').length")

    def paste(ext_id, text):
        pop = ctx.new_page(); pop.goto(f"chrome-extension://{ext_id}/popup.html")
        pop.wait_for_timeout(300)
        pop.fill("#config", text)
        pop.wait_for_timeout(400)
        status = pop.inner_text("#status")
        pop.close()
        page.bring_to_front()
        page.wait_for_timeout(2500)  # the previous copy steps aside and the new one draws (superseded copies re-check every 0.5 s)
        return status

    # Copy A (the first id that owns nothing yet) takes the page when its popup gets a config.
    paste(id_a, CONF_A)
    owner_a = owner()
    if owner_a != id_a: fails.append(f"after pasting into copy {id_a}, owner is {owner_a}")
    if overlays() != 1: fails.append(f"expected one overlay, got {overlays()}")

    # Copy B now: its popup must claim the page, and copy A must step aside.
    status_b = paste(id_b, CONF_B)
    owner_b = owner()
    if owner_b != id_b: fails.append(f"after pasting into copy {id_b}, owner is {owner_b}")
    if overlays() != 1: fails.append(f"after copy {id_b} took over, expected one overlay, got {overlays()}")
    if "Another installed copy" in status_b: fails.append("popup B warns about itself")

    # Copy A's popup must now warn that another copy is running here.
    pop = ctx.new_page(); pop.goto(f"chrome-extension://{id_a}/popup.html"); pop.wait_for_timeout(600)
    status_a = pop.inner_text("#status")
    pop.close()
    if "Another installed copy" not in status_a: fails.append("popup A does not warn about copy B on the page")

    print("owner after A:", owner_a == id_a, "| owner after B:", owner_b == id_b, "| overlays:", overlays())
    print("PASS" if not fails else "FAIL: " + "; ".join(fails))
    ctx.close()
sys.exit(1 if fails else 0)
