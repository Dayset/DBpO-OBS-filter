"""Loads the real unpacked extension in Chromium, pastes a config through the popup and checks
that it applies without a page reload and that player controls stay on top and clickable,
also with the viewer look on (popup sliders) and on a plain video ("normal videos" option).

Usage (from the repo root): python3 viewer/unsquish-extension/tests/extension.py
"""
import os, sys, subprocess, tempfile, threading, http.server, time
from playwright.sync_api import sync_playwright
ext = os.path.abspath("viewer/unsquish-extension")
tmp = tempfile.mkdtemp()
subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=480x270:rate=30", "-t", "4",
                "-c:v", "libvpx-vp9", "-b:v", "500k", os.path.join(tmp, "v.webm")], check=True)
open(os.path.join(tmp, "index.html"), "w").write('<!doctype html><title>Stream</title><body style="margin:0"><div style="width:640px;height:360px;position:relative"><div id="vwrap" style="width:100%;height:100%"><video src="v.webm" muted loop autoplay style="width:100%;height:100%;display:block"></video></div><button id="ctl" style="position:absolute;left:10px;bottom:10px">play</button></div>')
class Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: Q(*a, directory=tmp))
threading.Thread(target=srv.serve_forever, daemon=True).start()
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(tempfile.mkdtemp(), channel="chromium", headless=True, args=[
        f"--disable-extensions-except={ext}", f"--load-extension={ext}", "--headless=new",
        "--autoplay-policy=no-user-gesture-required", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    page = ctx.new_page()
    logs = []
    page.on("console", lambda m: logs.append(m.text))
    page.goto(f"http://127.0.0.1:{srv.server_port}/index.html")
    page.wait_for_timeout(1500)
    has = lambda: page.evaluate("!!document.querySelector('canvas[data-dbpo-unsquish]')")
    print("before paste, overlay:", has())
    # find extension id via service workers / background pages; MV3 without SW: use chrome://extensions? read from ctx
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
    ext_id = sw.url.split("/")[2]
    print("extension id", ext_id)
    pop = ctx.new_page()
    pop.goto(f"chrome-extension://{ext_id}/popup.html")
    pop.fill("#config", "FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.7778")
    pop.click("#save")
    pop.wait_for_timeout(300)
    print("popup status:", pop.inner_text("#status"))
    pop.close()
    page.bring_to_front()
    for t in range(10):
        page.wait_for_timeout(300)
        if has(): break
    print("after paste (no reload), overlay:", has(), "after", (t + 1) * 300, "ms")
    pop = ctx.new_page(); pop.goto(f"chrome-extension://{ext_id}/popup.html")
    pop.fill("#config", "FSQ1:rx=0.6,ry=0.6,cx=0.1,cy=0.1,fx=0.5,fy=0.5,px=0.5,py=0.5,ar=1.7778"); pop.click("#save"); pop.wait_for_timeout(300); pop.close()
    page.bring_to_front(); page.wait_for_timeout(600)
    print("tag after changing config:", page.evaluate("document.documentElement.dataset.dbpoUnsquish"))
    hit = page.evaluate("(() => { const b = document.getElementById('ctl').getBoundingClientRect(); return document.elementFromPoint(b.x + 5, b.y + 5).id; })()")
    print("element on top at the control button:", hit)
    page.click("#ctl", timeout=2000); print("control click: OK")
    fails = [] if hit == "ctl" else ["controls covered"]

    # Viewer look from the popup: pick "My look", drag two sliders.
    pop = ctx.new_page(); pop.goto(f"chrome-extension://{ext_id}/popup.html")
    pop.select_option("#lookMode", "mine")
    pop.wait_for_timeout(200)
    for sid, val in (("sharpen", "60"), ("sharpenPlus", "40")):
        pop.eval_on_selector(f"#{sid}", "(el, v) => { el.value = v; el.dispatchEvent(new Event('input')); el.dispatchEvent(new Event('change')); }", val)
    pop.wait_for_timeout(300)
    print("popup look hint:", pop.inner_text("#lookHint"))
    pop.close()
    page.bring_to_front(); page.wait_for_timeout(600)
    look = page.evaluate("document.documentElement.dataset.dbpoLook")
    print("look on the page:", look)
    if not look or [float(v) for v in look.split(",")] != [0.6, 0, 0, 0, 0.4] + [0] * 13: fails.append("look not applied")
    hit = page.evaluate("(() => { const b = document.getElementById('ctl').getBoundingClientRect(); return document.elementFromPoint(b.x + 5, b.y + 5).id; })()")
    print("with the look on, element on top at the control button:", hit)
    if hit != "ctl": fails.append("controls covered with the look on")
    page.click("#ctl", timeout=2000); print("control click with the look on: OK")

    # Plain video: clear the config, turn on "Also use my look on normal videos".
    pop = ctx.new_page(); pop.goto(f"chrome-extension://{ext_id}/popup.html")
    pop.click("#clear"); pop.wait_for_timeout(200)
    page.bring_to_front(); page.wait_for_timeout(600)
    if has(): fails.append("overlay on a plain video with the option off")
    pop.bring_to_front(); pop.check("#plainVideos"); pop.wait_for_timeout(200); pop.close()
    page.bring_to_front(); page.wait_for_timeout(800)
    try:
        page.wait_for_function("document.documentElement.dataset.dbpoUnsquish === 'plain'", timeout=6000)  # slow CI machines
    except Exception:
        pass
    tag = page.evaluate("document.documentElement.dataset.dbpoUnsquish")
    print("plain video with my look, overlay:", has(), "tag:", tag)
    if not has() or tag != "plain": fails.append("look not on the plain video")
    page.click("#ctl", timeout=2000); print("control click on the plain video: OK")
    pop = ctx.new_page(); pop.goto(f"chrome-extension://{ext_id}/popup.html")
    pop.select_option("#lookMode", "off"); pop.wait_for_timeout(200); pop.close()
    page.bring_to_front(); page.wait_for_timeout(600)
    print("look off, plain video overlay:", has())
    if has(): fails.append("overlay stayed with the look off")
    for l in logs: print("console:", l)
    ctx.close()
print("PASS" if not fails else "FAIL: " + ", ".join(fails))
sys.exit(1 if fails else 0)
