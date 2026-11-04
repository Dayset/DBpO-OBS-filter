# Sharp HUD regions (texture atlas) — spec

Status: draft for review. Tracks issue #2 (Atlas 1/8), child of epic #1. No product code is defined here; the C, shader, JavaScript and Python implementations must follow this document exactly.

Notation: all source coordinates are fractions in [0, 1] of the game capture before squishing. Output pixels are integers. `W, H` = stream (output) size. `bw, bh` = boxed squished picture size as fractions of `W, H` (1.0 = no box).

## 1. Atlas location

**Option A (recommended starting point):** keep the output at a standard size (1920×1080, 1280×720, 854×480 or 640×360). The squished picture is shrunk into a box (`bw` or `bh` < 1, aspect preserved) and the atlas sits in the freed strip (right column when `bw` < 1, bottom row when `bh` < 1).

**Option B:** keep the squished picture at full size and append the strip, giving a non-standard output such as 1920×1180. Keeps center gain, but Twitch handles non-standard sizes poorly, so it is not the default.

Limit for Option A: the strip may use at most 20% of the output width (or height). The earlier black-bar padding cost about half the center gain; the atlas must cost much less than that, so the strip cap is enforced at parse time.

## 2. Region definition

Each region `i` is `(x0, y0, x1, y1)` in source fractions plus a scale `s_i`.

- Maximum regions: 4. Minimum region size: 0.02 of the source per axis. Maximum: 0.5 per axis.
- Overlapping regions are not allowed; the filter rejects the config and reports the pair.
- `s_i` = 1.0 means 1:1 with source pixels.
- Floor per axis: `s_min = (d + 1) / 2`, where `d` is the local squish density from `squish_density()` at the region centre (`d` = 1 means no squish there). This guarantees at least 50% less squish in the region.
- If the regions do not fit at their requested scale, all scales are reduced by the same factor, never below the floor. If the floor does not fit, the filter refuses and states the reason.
- A region fully inside the sharp centre is allowed but the settings UI shows a warning ("already sharp, region adds nothing").

## 3. Deterministic packing

Input: stream size `(W, H)`, strip side (right or bottom), region list in config order.

1. Slot size in output pixels: `w_i = floor((x1-x0) * src_w * s_i * k)`, `h_i` likewise, where `k` is the stream/source scale (`W / src_w` on the box axis). Floor, never round.
2. Snap every slot width and height up to a multiple of **8 px** (encoder macroblock friendly; 16 px is rejected because it wastes strip area at 360p).
3. Shelf packing in config order: place slots left-to-right in rows of the strip, starting at the strip origin. A new row starts when the next slot does not fit. Largest-first is disallowed (ties would need extra rules).
4. Slot origin is always even on both axes (4:2:0 chroma).
5. Gutter: each slot has a 4 px gutter on every side, filled with an edge-clamped copy of the slot (not black), so bilinear sampling and chroma subsampling cannot bleed neighbours.
6. If packing does not fit the strip, step 2 of section 2 applies (scale down, never below the floor).

## 4. Duplicated area in the main picture

The region also appears inside the squished picture. The atlas option `blank duplicate` fills that area with a cheap-to-encode fill (flat mean colour of the region). Default is **on** for Option A, because the duplicate otherwise costs bits twice. Viewers without the extension see a flat patch; the streamer's warning in the UI says so. The default is confirmed by research issue #4.

## 5. Config string

- Format unchanged: `FSQ1:` followed by `key=number` pairs, keys exactly two lowercase letters.
- New keys: a region is five numeric keys, `a0..a3` (x0, y0, x1, y1) and `s0` (scale), with region index as the digit (`a0` … `a3`, `s0`, then `b0..` for region 2 — exact scheme to be fixed in #5). Values use 3 decimals.
- Old parsers: the current `parse()` in `viewer/unsquish-extension/squish.js` matches `[a-z]{2}=number` pairs only, so a colon-separated region value would break the whole match. **Decision:** every region number is its own `key=number` pair so existing parsers still match and ignore unknown keys. Confirmed by a test in #5.
- Length budget: a typical config (2 regions, boxed layout, two look keys) must be ≤ 140 characters to fit a Twitch title. Worst case (4 regions, all keys) is documented in the table below.
- A config with 0 regions must produce a string byte-identical to today's output.

| Case | Example | Length |
|---|---|---|
| Today (no regions) | `FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.7778` | 76 |
| 2 regions, boxed, 2 look keys (typical) | to be measured in #5 | target ≤ 140 |
| 4 regions, boxed, all look keys (worst) | to be measured in #5 | recorded there |

Lengths are measured by the Python reference (#3), not estimated by hand.

## 6. Viewer composition

Order of operations:

1. Unsquish the picture (existing path).
2. Paste the atlas slots at their normalised rectangles.
3. Apply the viewer's look **once** over the composed picture. Running it twice would double-sharpen the region; once keeps the slot and the surroundings consistent.

Edges: hard edge by default; an optional 1–2 px feather is a viewer setting.

Scaling: slot rectangles are stored in normalised stream coordinates, so a player that scales the stream (e.g. to 720p) keeps them correct. This is verified by the worked examples below.

## Worked examples

Source 2560×1440, stream 1920×1080 (Option A, `bw = 0.80`, right strip 384 px wide before gutters). One region `r0 = (0.80, 0.85, 1.00, 1.00)` (bottom-right HUD corner, 512×216 source px).

- Region source size: 512 × 216 px.
- Slot at `s = 1.0` scaled to the stream: `512 × 0.75 = 384` → 384 × 162 px, snapped to 8 px: 384 × 168 px.
- Strip is 384 px wide: fits in one row. Origin (1536, 0), gutter 4 px, slot rectangle (1540, 4) → (1924, 172).

720p stream (1280×720): the same region scales to 256 × 108 → snapped 256 × 112 px at origin (1024, 0). Normalised rectangle is unchanged.

360p stream (640×360): 128 × 54 → snapped 128 × 56 px at origin (512, 0). Each slot is still at least 2 snapped units wide, so the 4 px gutter is kept.

Reference values for each case are checked by the Python implementation in #3.

## Open questions for research (#4)

1. Does Option A (boxed squish + strip) keep at least half of the center gain at 250 kbps and 300 kbps?
2. Does "blank duplicate" as flat mean colour cost less than leaving the duplicate in place, on real HUD footage?
3. Is 8 px snapping worth its strip-area cost compared with 4 px at 1080p?
4. Does the strip cap of 20% hold for 1080p and 720p streams?

## Acceptance checklist (#2)

- [x] Sections 1–6 cover location, regions, packing, duplicate handling, config string and viewer composition, with formulas.
- [x] Worked examples for 1080p (from 1440p source), 720p and 360p with slot rectangles.
- [x] String-length table (today, typical, worst case). Typical and worst values are filled by #3.
- [x] Open questions listed for #4.
- [ ] Repo owner review before #5, #6 and #8 start.
