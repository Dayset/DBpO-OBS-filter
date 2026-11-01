"""Draws the setup guide pictures (docs/*.png) with headless Chromium.

Usage: python3 plugins/foveated-squish/docs/make_figures.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "research", "foveation"))
from squish_math import Axis  # noqa: E402

# (file, title, monitor, game res, dldsr note, canvas, layout, squeeze note)
SCENARIOS = [
    ("1440p-monitor-1080p-stream", "1440p monitor → 1080p stream", "2560×1440 monitor", (2560, 1440), None,
     (1920, 1080), "Fill the canvas", "75% — nothing extra to do"),
    ("1080p-monitor-1080p-stream", "1080p monitor → 1080p stream", "1920×1080 monitor", (2560, 1440),
     "NVIDIA DLDSR 1.78x", (1920, 1080), "Fill the canvas", "75%"),
    ("1440p-monitor-1440p-stream", "1440p monitor → 1440p stream", "2560×1440 monitor", (3413, 1920),
     "NVIDIA DLDSR 1.78x", (2560, 1440), "Fill the canvas", "75%"),
    ("4k-monitor-1440p-stream", "4K monitor → 1440p stream", "3840×2160 monitor", (3840, 2160), None,
     (2560, 1440), "Fill the canvas", "67% (center auto-trimmed to ~28%)"),
    ("1080p-monitor-no-dldsr", "1080p monitor, no DLDSR → 1440×810 stream", "1920×1080 monitor", (1920, 1080), None,
     (1440, 810), "Exact size, 75%", "75% — type 1440x810 into both fields"),
]

CSS = """
:root { --bg:#f6f7f9; --card:#fff; --ink:#1b1e24; --muted:#5b6270; --line:#d8dce3; --accent:#2f6fec;
        --sharp:#2bb673; --squeeze:#e8833a; --obs:#2b2e38; --obs2:#3a3e4b; --obsink:#e6e8ee; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); font: 15px/1.4 "Segoe UI", system-ui, sans-serif; color:var(--ink); }
.wrap { width: 1200px; padding: 28px 32px 30px; }
h1 { font-size: 26px; margin: 0 0 4px; }
.sub { color: var(--muted); margin-bottom: 22px; }
.row { display:flex; align-items:stretch; gap: 14px; }
.step { background:var(--card); border:1px solid var(--line); border-radius: 12px; padding: 14px 16px; flex:1; }
.step h2 { font-size: 13px; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); margin:0 0 10px; }
.num { display:inline-flex; width:22px; height:22px; border-radius:50%; background:var(--accent); color:#fff;
       font-size:12px; align-items:center; justify-content:center; margin-right:6px; }
.big { font-size: 26px; font-weight: 700; }
.note { color: var(--muted); font-size: 13px; margin-top: 6px; }
.tag { display:inline-block; background:#eaf1ff; color:var(--accent); border-radius: 6px; padding: 2px 8px; font-size: 13px; font-weight:600; margin-top:8px; }
.arrow { display:flex; align-items:center; color: var(--muted); font-size: 28px; }
.obs { background: var(--obs); color: var(--obsink); border-radius: 8px; padding: 12px; font-size: 13px; }
.obs .title { color:#aab0bd; margin-bottom: 8px; }
.field { display:flex; justify-content:space-between; align-items:center; margin: 6px 0; gap: 10px; }
.field .v { background: var(--obs2); border: 2px solid #ffcc33; border-radius: 4px; padding: 3px 8px; font-weight:700; color:#fff; min-width: 110px; text-align:center; }
.legend { display:flex; gap: 16px; margin-top: 16px; color: var(--muted); font-size: 13px; }
.sw { display:inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 5px; vertical-align: -1px; }
svg text { font-family: "Segoe UI", system-ui, sans-serif; }
"""


def frame_svg(w, h, axis_x, axis_y, squished, label, s):
    """A frame drawn with a grid (s = drawing scale, shared by both frames so sizes compare)."""
    W, H = w * s, h * s
    n = 16
    xs = [i / n for i in range(n + 1)]
    fx = (lambda v: axis_x.forward(v)) if squished else (lambda v: v)
    fy = (lambda v: axis_y.forward(v)) if squished else (lambda v: v)
    lo_x, hi_x = fx(axis_x.f - axis_x.c), fx(axis_x.f + axis_x.c)
    lo_y, hi_y = fy(axis_y.f - axis_y.c), fy(axis_y.f + axis_y.c)
    parts = [f'<svg width="{W + 2:.0f}" height="{H + 2:.0f}" viewBox="-1 -1 {W + 2:.1f} {H + 2:.1f}">',
             f'<rect x="0" y="0" width="{W:.1f}" height="{H:.1f}" fill="#fdf1e7" stroke="#c9ced8"/>',
             f'<rect x="{lo_x * W:.1f}" y="{lo_y * H:.1f}" width="{(hi_x - lo_x) * W:.1f}" height="{(hi_y - lo_y) * H:.1f}" fill="#e3f6ec" stroke="#2bb673" stroke-width="1.5"/>']
    for v in xs:
        x = fx(v) * W
        y = fy(v) * H
        parts.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{H:.1f}" stroke="#e8833a" stroke-opacity=".45" stroke-width="1"/>')
        parts.append(f'<line x1="0" y1="{y:.1f}" x2="{W:.1f}" y2="{y:.1f}" stroke="#e8833a" stroke-opacity=".45" stroke-width="1"/>')
    parts.append("</svg>")
    return "".join(parts) + f'<div class="note">{label}</div>'


def scenario_html(sc):
    _, title, monitor, game, dldsr, canvas, layout, squeeze = sc
    gw, gh = game
    cw, ch = canvas
    rx, ry = cw / gw, ch / gh
    scale = 300 / gw
    ax, ay = Axis(r=rx, c=0.15), Axis(r=ry, c=0.15)
    for a in (ax, ay):  # same auto-trim as the plugin (shrink the center to fit)
        while not a.valid() and a.c > 0.01:
            a.c -= 0.0025
    game_note = (f'Set <b>{dldsr}</b> in the NVIDIA Control Panel, then pick <b>{gw}×{gh}</b> in the game'
                 if dldsr else f'Game at <b>{gw}×{gh}</b> (native)')
    if layout.startswith("Exact"):
        game_note = f'Game at <b>{gw}×{gh}</b> (native)'
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body><div class="wrap">
<h1>{title}</h1>
<div class="sub">{monitor} · squeeze {squeeze}</div>
<div class="row">
  <div class="step"><h2><span class="num">1</span>Game</h2>
    <div class="big">{gw}×{gh}</div><div class="note">{game_note}</div>
    <div style="margin-top:12px">{frame_svg(gw, gh, ax, ay, False, "what the game renders", scale)}</div></div>
  <div class="arrow">→</div>
  <div class="step"><h2><span class="num">2</span>OBS filter</h2>
    <div class="big" style="font-size:20px">Foveated Squish</div>
    <div class="note">on the Game Capture source<br>then right-click → Transform → <b>Reset Transform</b> (Ctrl+R)</div>
    <div class="tag">Layout: {layout}</div>
    <div style="margin-top:12px">{frame_svg(cw, ch, ax, ay, True, f"what gets streamed: {round(100 * cw * ch / (gw * gh))}% of the pixels, same center", scale)}</div></div>
  <div class="arrow">→</div>
  <div class="step" style="flex:1.05"><h2><span class="num">3</span>OBS Settings → Video</h2>
    <div class="obs"><div class="title">Video</div>
      <div class="field"><span>Base (Canvas) Resolution</span><span class="v">{cw}x{ch}</span></div>
      <div class="field"><span>Output (Scaled) Resolution</span><span class="v">{cw}x{ch}</span></div></div>
    <div class="note" style="margin-top:10px">Both the same. Keep your usual bitrate.</div>
    <div class="note">Viewers: paste the filter's <b>FSQ1:…</b> string into the DBpO Unsquish extension (or put it in the stream title).</div></div>
</div>
<div class="legend"><span><span class="sw" style="background:#e3f6ec;border:1.5px solid #2bb673"></span>sharp center, 1:1 pixels</span>
<span><span class="sw" style="background:#fdf1e7;border:1px solid #e8833a"></span>squeezed edges (grid lines show how much)</span></div>
</div></body></html>"""


def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1200, "height": 600}, device_scale_factor=2)
        for sc in SCENARIOS:
            page.set_content(scenario_html(sc))
            page.locator(".wrap").screenshot(path=os.path.join(HERE, sc[0] + ".png"))
            print("wrote", sc[0] + ".png")
        b.close()


if __name__ == "__main__":
    main()
