# Foveated Squish

Squeezes the **edges of the picture (X and Y)** while the center keeps exact 1:1 pixels, so a stream with a fixed bitrate (CBR) spends its bits where the viewer looks. Viewers unfold the picture with the **DBpO Unsquish** browser extension ([viewer/unsquish-extension](../../viewer/unsquish-extension/README.md)).

Why it works: at CBR the encoder spends the same number of bits per second regardless of the picture size. A 75% × 75% frame has 56% of the pixels, so each remaining pixel gets ~1.8× the bits. In the center nothing was removed, so all of that goes into quality. At the edges a squeezed pixel stands for several source pixels, which looks like a soft blur once unfolded. That is roughly what peripheral vision sees anyway. Unlike encoder ROI ("please spend more here"), this forces it.

Measured (x264 veryfast, 960×540 synthetic FPS pan, 60 fps; see [research/foveation](../../research/foveation/README.md)):

| CBR 250 kbps, clean | streamed | fovea | middle ring | outer |
|---|---|---|---|---|
| no squish | 960×540 | 35.0 dB | 35.7 dB | 36.1 dB |
| 75% × 75%, linear (default) | 720×404 | **37.6** | 36.8 | 34.4 |
| 75% × 75%, curve 1.5 | 720×404 | **37.7** | **38.0** | 33.9 |
| 65% × 65%, curve 0.5 | 624×352 | **38.9** | 38.1 | 34.0 |

## Setup

Pick a **Layout** in the filter:

| Layout | Canvas | Center gain | When |
|---|---|---|---|
| **Fill the canvas** (default) | your normal canvas: 1920×1080, 2560×1440, … | full | the game is captured at a **higher** resolution than the canvas |
| Black bars | any canvas, same size as the source | about half | quick try, nothing else to change |
| Exact size | type the squished size into the canvas field | full | you want a specific squeeze with a matching canvas |

**Fill the canvas** squeezes exactly canvas ÷ source, so the picture fills a standard canvas with no bars:

| Game / capture | Canvas | Squeeze |
|---|---|---|
| 2560×1440 | 1920×1080 | 75% (the measured sweet spot) |
| 3840×2160 | 2560×1440 | 67% |
| 3840×2160 | 1920×1080 | 50% (sharp center shrinks automatically to make it fit; the info line says to what) |
| 3440×1440 ultrawide | 1920×1080 | 56% × 75% (the extension restores 21:9) |

Steps:
1. Run (or capture) the game at the higher resolution, e.g. 2560×1440, and keep **Base (Canvas)** and **Output (Scaled)** at 1920×1080.
2. Add **Foveated Squish** (Effect Filters) to the game capture source, or to a group/nested scene that holds everything you stream. The source becomes exactly canvas-sized; press *Ctrl+F* (Fit to screen) on it if it was scaled before.
3. Keep the bitrate you already use: same bits, fewer pixels per frame.
4. Click **Copy config to clipboard** and give viewers the `FSQ1:…` string: put it in the stream title (the extension finds it automatically) or send it via a messenger.

**Black bars**: the squished picture is centered on black bars in a frame of the source size, so it fits a canvas the same size as the source. It's easy, but the bars still cost the encoder bits. At 250 kbps it measured +1.3 dB in the center vs +2.6 dB for Fill/Exact (grainy clip: +1.9 vs +2.6). The bar position is part of the config string, so the extension crops it exactly.

**Exact size**: OBS's canvas field accepts typed values. Type the squished size shown in the info line (e.g. `1440x810`) into both Base and Output resolution.

To load someone else's config (or your saved one), paste it into **Paste a config string** and click **Load pasted config**.

## Settings

| Setting | Meaning |
|---|---|
| Layout | Fill the canvas / Black bars / Exact size (see Setup). |
| Streamed width / height | Bars and Exact only: squished size, % of the source. 75% is a good start; lower = sharper center, softer edges. (Fill derives it from the canvas.) |
| Sharp center width / height | The region kept at exact 1:1 pixels (% of the frame). |
| Focus position | Where the sharp center sits (crosshair = 50/50). |
| Curve | How the squeeze ramps from the center edge to the frame edge. 1 = linear. Above 1: the ring right outside the center stays almost sharp and the far edges take the squeeze (best middle ring in tests). Below 1: squeeze starts right away, which allows stronger total squeeze. |
| Same settings for height | Links the vertical settings to the horizontal ones. |
| Area filter | Averages all source pixels a squeezed pixel covers. Prevents shimmer/aliasing at the edges; keep on. |
| View | *Squished* (what is streamed), *Unsquished preview* (what extension viewers see), *Squeezed zone* (red = squeezed harder). |

If you ask for more squeeze than the curve and center size allow (the edge density would drop below 8%), the filter limits it and says so. Lower the curve value or the center size to go further.

## Config string

```
FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.7778
```

`r` = squished/source size, `c` = half-size of the sharp center, `f` = focus position, `p` = curve, `ar` = source aspect ratio (the extension shows the unfolded picture at this shape). With black bars (or Fill when the source is smaller than the canvas) the string ends with `ox,oy,bw,bh`: where the picture sits in the streamed frame. The same mapping is implemented in the shader, in C (`src/squish-math.c`), in the extension (`squish.js`) and in Python (`research/foveation/squish_math.py`); the tests check all of them against the Python reference.

## Limits

- Viewers without the extension see a squeezed picture: center normal, edges compressed. It's watchable but odd, so tell them.
- The center is sharp for the configured focus point only. Per-viewer gaze would need a separate stream per viewer (see the tile notes in the research README).
- Chat overlays and webcams inside the squished source get squeezed too. Put them inside the sharp center, or accept that.
