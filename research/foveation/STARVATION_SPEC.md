# Starvation levels — spec (issue #11, epic #10)

One spec for the Python reference (`starvation_math.py`), the OBS filter (C + shader), the extension and the
userscript. Numbers come from `starvation_findings.md`; where this file changes one, the reason is given.
Reference: `research/foveation/starvation_math.py`, golden vectors: `starvation_golden.json`.

## 1. Idea in one paragraph

The stream frame never changes size. When starved, the filter draws the picture smaller, in the **top-left**
of the frame, fills the rest with **black**, and stamps a **marker** that says which level was used. The viewer
reads the marker, crops the picture rect and scales it back up. Viewers without the extension see a small
picture top-left (documented).

## 2. Levels

A level is a fraction `f` of the *picture rect* (the whole frame, or the picture rect of a squish/atlas
layout: `picture_rect` from `atlas_math.layout`, always at (0,0)).

| code | name | kind | f (of picture rect) | 1920x1080 | 1712x964 | 1280x720 |
|---|---|---|---|---|---|---|
| 0 | full | plain | 1 | 1920x1080 | 1712x964 | 1280x720 |
| 1 | 720 | plain | 2/3 | 1280x720 | 1140x642 | 852x480 |
| 2 | 540 | plain | 1/2 | 960x540 | 856x482 | 640x360 |
| 3 | 360 | plain | 1/3 | 640x360 | 570x320 | 426x240 |
| 4 | squish-810 | squish | 3/4 | 1440x810 | 1284x722 | 960x540 |
| 5 | squish-540 | squish | 1/2 | 960x540 | 856x482 | 640x360 |
| 6–15 | reserved | – | – | – | – | – |

- **Rounding rule:** `w = round(f * pw)` (half away from zero), then cleared to even (`w & ~1`); same for `h`. No
  multiple-of-16 rule: top-left placement keeps the picture MB-aligned at the origin, and only the far edge
  of the picture falls inside a macroblock, which the measurements (black padding ≈ real low-res stream, within 0.05 dB) did not
  penalise. The exact sizes are in the golden file; implementations must reproduce them.
- **Plain vs squish:** if the filter runs in plain mode (no FSQ1 geometry), only codes 0–3 are used. If the
  squish geometry is on, the unsquished-picture level is the picture rect of that layout; codes 0–3 then shrink
  the already squished picture further (area downscale), and codes 4–5 are the *squish into a smaller rect*
  variants measured in the bench (`sq810`, `sq540`), which re-run the squish mapping with `r = pw / source_w`
  (strongest centre, `starvation_findings.md`: squish into 960x540 = sharpest centre at 6000 kbit/s).
  In plain mode, codes 4–5 are not allowed (sender never emits them; viewer treats them as level 0).
- For codes 4–5, `f` is a fraction of the **stream frame** (the squished picture fills the rect), for codes 0–3 of the
  picture rect.
- Dropping `f` below 1/3 is not offered (360p at 3000 kbit/s was the lowest measured gain; the streamer's
  "lowest allowed level" can only be 720/540/360).

## 3. Layout

- Picture at (0, 0), size from §2.
- Everything else in the stream frame: **black**, i.e. studio black for the output range (limited: Y=16,
  U=V=128; full: Y=0). Never edge-repeat (−0.3…−0.8 dB), mirror (−4 dB), or centred (worse).
- With an atlas strip (#3): the strip and its slots **stay where they are at every level** (not drawn
  smaller). Reason: slots exist so small regions (minimap, HUD text) stay readable, they are mostly static
  and cheap for the encoder, and the strip is capped at 20 % of the frame. Only the picture rect shrinks. The
  viewer's atlas code is unchanged; it gets the picture rect cropped at the level.

## 4. Marker

- 5 bits: bits 0–3 = code (LSB first), bit 4 = even parity of bits 0–3 (so the number of 1s in the 5 bits is even).
- **Manchester, 2 cells per bit:** bit 1 = white cell then black cell, bit 0 = black then white. 10 cells, each
  16x16 px, so the strip is 160x16.
- Position: `x = W − 160`, `y = floor((H − 24) / 16) * 16` (1056 for H=1080, 928 for H=964, 688 for H=720).
  `W`, `H` = stream frame size. Always drawn **last**, so at level 0 it covers 160x16 of the picture (accepted).
- Colours: white = luma 235, black = luma 16, neutral chroma. In the RGB the filter renders (before the encoder's limited-range
  conversion) that is white = 255, black = 0; the studio-black padding of §3 is RGB 0 for the same reason.
- **Decode:** for each cell take the mean luma of the inner 8x8 (offset 4 in the cell); bit = (first > second).
  Reject on parity failure. If the two cells of a bit are within 4 luma levels of each other the bit is
  unreadable → reject (new vs the bench; costs nothing and guards against a missing marker on a blank frame).
- No FSQ1 key is required to *draw* the marker; it is drawn whenever starvation levels are enabled.

## 5. Config key

One new key in the FSQ1 tag: `sl=1` (absent or 0 = starvation levels off). The extension looks for the
marker only when `sl=1`, so an older streamer or a stream with the feature off costs nothing. Two letters,
like the other keys. The code table (§2) is fixed in this spec and **not** sent.

## 6. Viewer behaviour

- Read the marker every decoded video frame. Valid marker → level for this frame, store it as `last`.
- No valid marker (parity fail, unreadable): keep `last` for at most **N = 12 frames at 60 fps (0.2 s)**,
  scaled by frame rate (`N = round(0.2 * fps)`, minimum 3); after that fall back to level 0.
  Reason: a one-frame bit error (measured 0 in 3 h of test clips, but ingest transcodes exist) must not flash,
  while a stream that really has no marker must not stay cropped for seconds.
- Level change applies on the **same frame** the marker changes (read before drawing; no one-frame lag).
- Unknown/reserved code → level 0.
- Upscale: bilinear, from the picture rect to the player size. Squish codes: crop the rect and run the
  normal unsquish on it. Interaction with colour settings and atlas: same code path as today, applied after the crop.

## 7. Sender behaviour (summary, details in #12/#14)

- A level change takes effect on the next frame, **no keyframe request** (measured: free mid-GOP).
- The decision is made by the detector (#12); manual force by hotkey (#16).
- Rendering is the area downscale of §2 into the rect, black elsewhere, marker last.

## 8. Test vectors

`starvation_golden.json` has: rects per level for 1920x1080, 1712x964, 1280x720; marker bit patterns for each code; a few
pixel samples of `render` and `view` for a fixed synthetic image. C, shader and JS compare against it.
