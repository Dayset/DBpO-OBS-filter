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
| Uniform 75% downscale + upscale | 36.32 / 33.63 (+0 / +1.9 dB) | 35.09 / 32.44 | nothing (lower resolution) |
| Edge softening (blur) | 36.30 / 31.55 | 35.42 / 30.90 | nothing |
| Line doubling in place, X | 36.30 / 31.28 | 34.55 / 30.32 | nothing |
| Line doubling in place, X+Y | 36.07 / 30.95 | 33.66 / 29.94 | nothing |

- **The squish works as intended at a fixed bitrate:** removing pixels from the sides forces the encoder to spend the budget on the center, +1.2 to +2.7 dB there (roughly what 30–50% more bitrate would buy). X+Y beat X-only for the center.
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

## Caveats

Synthetic content, one squish profile, x264 only (no NVENC here). Re-measure on a lossless gameplay recording before deciding. The squish needs a viewer-side unwarp (custom player, MPV shader, or a browser extension that re-renders the page's `<video>` through WebGL); viewers without it see the squeezed picture.
