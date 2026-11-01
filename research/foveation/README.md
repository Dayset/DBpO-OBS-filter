# Foveation research: squish, edge softening, periphery frame-rate rings

Measures the "squish shader" ideas (geometric warp of the sides before encoding, unwarp for the viewer) against alternatives that need no viewer-side player. Uses the same synthetic game-like clips as `plugins/perceptual-color-reduction/tests/bitrate_harness.py` (960×540, terrain pan, HUD, moving sprites, optional film grain), **x264 veryfast**. The center 30% of the frame width is untouched in every variant; the squish profile ramps smoothly from 1:1 at the center to 3.5:1 at the edges (960 → 720 px).

```
python3 research/foveation/foveation_bench.py cbr 60   # fixed bitrate: where the bits go
python3 research/foveation/foveation_bench.py crf 60   # fixed quality: bits saved (+ 120 fps rings)
```

## Fixed bitrate (CBR, like a stream): the question that matters for streaming

Luma PSNR of the decoded (and, where needed, unwarped) result vs. the original.

| Variant | Center PSNR clean / grainy | Edge PSNR clean / grainy | Viewer needs |
|---|---|---|---|
| Baseline (250 / 300 kbps) | 36.30 / 31.71 | 35.71 / 30.81 | nothing |
| **Squish X+Y 75%/75% + unwarp** | **38.07 / 34.37** (+1.8 / +2.7 dB) | 34.38 / 31.96 | unwarp player |
| Squish X 75% + unwarp | 37.53 / 33.20 (+1.2 / +1.5 dB) | 35.17 / 31.63 | unwarp player |
| Discard X+Y: drop rows/cols, no filter, same unwarp | 38.08 / 34.15 (+1.8 / +2.4 dB) | 34.19 / 31.58 | unwarp player |
| Uniform 75% downscale + upscale | 36.32 / 33.63 (+0 / +1.9 dB) | 35.09 / 32.44 | nothing (lower resolution) |
| Edge softening (blur) | 36.30 / 31.55 | 35.42 / 30.90 | nothing |
| Line doubling in place, X | 36.30 / 31.28 | 34.55 / 30.32 | nothing |
| Line doubling in place, X+Y | 36.07 / 30.95 | 33.66 / 29.94 | nothing |

- **The squish works as intended at a fixed bitrate:** removing pixels from the sides forces the encoder to spend the budget on the center, +1.2 to +2.7 dB there (roughly what 30–50% more bitrate would buy). X+Y beat X-only for the center.
- Dropping whole rows/columns (nearest sampling, no pre-filter) gives the same center gain as the smooth squish; edges are slightly worse (aliasing/shimmer). It's an implementation choice, not a quality one.
- **In-place line doubling ("empty rows/columns") gives no center gain:** duplicated pixels are not cheap enough for the encoder to move bits. The pixels must actually leave the frame.
- Edge softening doesn't move bits to the center either; its benefit is bits saved at fixed quality (below).

## Fixed quality (CRF): bits saved

| Variant (60 fps) | CRF 23 clean / grainy | CRF 18 clean / grainy |
|---|---|---|
| Edge softening | +0.1% / −2.0% | −1.1% / −13.8% |
| Squish X 75% + unwarp | −5.6% / −7.3% | +7.4% / −28.1% |
| Uniform 75% downscale | −15.5% / −16.7% | −16.8% / −58.3% |

At fixed quality the encoder simply uses fewer bits, so these numbers say nothing about center sharpness on a stream; use the CBR table for that.

## Periphery frame-rate rings (freeze-filter idea), 120 fps stream, CRF 23

| Variant | Clean pan | Grainy pan |
|---|---|---|
| Periphery held to 60 fps (feathered) | **+12.2%** | +2.7% |
| Periphery held to 40 fps (feathered) | **+23.2%** | −2.7% |
| Plain 60 fps stream | −11.9% | −22.0% |

Held edges are not static during camera pans: they jump 2–3 frames of motion at once and the feather blends two moments in time, which costs bits. This also explains the tearing seen at ring borders.

## Squish curves (the plugin's exact mapping), X+Y, sharp center 30%

`squish_profiles.py [frames] [kbps,...]` — exact area-filtered squish, bilinear unsquish (what the extension does). Luma PSNR: fovea (center box ±15%), middle ring (15–30%), outer.

| CBR budget, clip | no squish | 75%, curve 0.5 | **75%, linear (default)** | 75%, curve 1.5 | 65%, curve 0.5 |
|---|---|---|---|---|---|
| 250 kbps clean: fovea / middle / outer | 35.0 / 35.7 / 36.1 | 37.2 / 36.2 / 34.6 | **37.6 / 36.8 / 34.4** | 37.7 / 38.0 / 33.9 | 38.9 / 38.1 / 34.0 |
| 300 kbps grain | 31.4 / 31.2 / 31.0 | 33.8 / 33.4 / 32.3 | **34.0 / 33.9 / 32.0** | 33.8 / 34.1 / 31.6 | 34.9 / 34.7 / 32.1 |
| 1500 kbps clean | 43.6 / 43.8 / 44.1 | 43.9 / 39.5 / 37.0 | 43.9 / 41.3 / 37.0 | 43.8 / 43.2 / 36.2 | 43.9 / 42.4 / 35.7 |
| 1500 kbps grain | 36.9 / 37.1 / 37.1 | 37.5 / 36.0 / 34.6 | 37.5 / 36.7 / 34.6 | 37.5 / 37.2 / 34.1 | 37.6 / 37.0 / 33.7 |

- **The squish only pays when the stream is starved.** At 250–300 kbps (this clip needs ~340 kbps for CRF 23) the center gains +2.5 dB and even the middle ring improves. At 1500 kbps the encoder already has more bits than this simple clip can use (center 43.6 dB, near transparent), so there is nothing to win and the edges just lose detail. Real game streams at 6 Mbps 1080p60 are much closer to the starved case (visible blocking in motion), but this has to be confirmed on real footage.
- **Black bars instead of a smaller frame** (squished 720×404 centered in 960×540 black, 250 kbps): fovea 36.3 dB with the picture placed on 16-px blocks, 35.8 dB unaligned, vs 37.6 dB at the exact size and 35.0 dB without squish. Grain: 33.3 vs 34.0 vs 31.4. Bars keep roughly half the gain, so the plugin doesn't offer them: it fills a standard canvas by squeezing canvas ÷ source (game rendered above the canvas size, e.g. with DLDSR).
- Curve 1.5 keeps the middle ring sharpest; curve 0.5 allows the strongest squeeze (65%) and the best center. Linear is the balanced default.

## Bitstream-level foveation: tiles and "server-side packet loss"

`tiles_and_slices.py [frames] [kbps,...]`

**Tiles (3×3 grid, every tile an independent stream, raw H.264, no container).** This is the independence HEVC/AV1 motion-constrained tiles need for per-viewer tile selection.

| | clean | grain |
|---|---|---|
| Overhead at equal CRF (23 / 30) | +37% / +47% | +51% / +43% |
| Plain CBR 250 kbps: fovea / outer | 35.0 / 36.0 dB | 30.3 / 29.4 dB |
| Edges CRF 46 + center gets the rest (same 250 kbps) | **41.2** / 27.8 dB | 34.2 / 26.2 dB |
| Edges CRF 51 + center gets the rest | 42.6 / 25.6 dB | 35.4 / 25.8 dB |
| Squish 75% linear (for comparison) | 37.6 / 34.4 dB | 34.0 / 32.0 dB |

- Tiles can starve the edges much harder than the squish, so the center can get far more bits (+6 dB on clean). But the edges become blocky, not soft (outer 26–28 dB vs 34 dB with the squish), and the tile borders show seams. Even at the worst quality, the 8 edge tiles still cost ~130 kbps, because every tile pays per-frame headers and loses prediction across borders. The overhead here (+40–50%) is inflated by small 320×180 tiles at a very low bitrate; real HEVC/AV1 tiles inside one stream are typically quoted at 5–15%.
- Tiles are the only clean way to give each viewer a **personal fovea without re-encoding** (gaze-dependent tile selection). The cost: HEVC/AV1 with motion-constrained tiles, a server that rewrites the tile set per viewer, a player that can decode it, and a fovea that can only move on tile boundaries and only at keyframes.

**Slice dropping (the "broken in-between frames" idea).** A normal CBR stream with 6 horizontal slices; in every non-key frame the top and bottom slice are deleted on the server, like provoked packet loss. The decoder conceals the holes.

| | 250 kbps clean | 250 kbps grain | 1500 kbps clean | 1500 kbps grain |
|---|---|---|---|---|
| Bytes removed | 19% | 18% | 24% | 23% |
| Center (fovea) quality change | 0.00 dB | 0.00 dB | 0.00 dB | 0.00 dB |
| Kept bands, late in the GOP (drift) | −0.01 dB | −0.01 dB | −0.10 dB | −0.01 dB |
| Dropped bands | −7.8 dB | −4.0 dB | −17.2 dB | −10.6 dB |

- **It works as a per-viewer bandwidth cut:** about 20% fewer bytes, the decoder doesn't crash, and the center is untouched. With this horizontal pan almost nothing leaks into the kept area. Vertical camera motion would leak more, because H.264 motion vectors may point into the missing bands.
- **It does not make the center sharper.** The encoder spent those bits before they were thrown away, so the viewer gets the same center at a lower bitrate. That differs from the squish and tiles, where the saved bits are re-spent on the center.
- The dropped bands **freeze at the last keyframe** and smear with concealment until the next one (every 2 s here). They don't look blurred, they look broken, which is very visible in peripheral vision during motion.
- H.264 slices are horizontal row bands only, so the **left/right edges can't be dropped this way**. That needs HEVC/AV1 tiles (above).

## Caveats

Synthetic content, x264 only (no NVENC here). Re-measure on a lossless gameplay recording before deciding. The squish needs a viewer-side unwarp (custom player, MPV shader, or a browser extension that re-renders the page's `<video>` through WebGL); viewers without it see the squeezed picture.
