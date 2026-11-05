# Pixel Snapper — measured (Nov 2026)

Claim under test (from a pasted "Pixel Snapper" write-up): palette quantization + Sobel edge mask + grid snap /
flat-area merge + nearest-neighbor resample + temporal dither lowers encoder bitrate by 20–35%.

Method: `pixel_snapper_bench.py` models each stage separately and combined, x264 veryfast CRF 18/23, same synthetic
clips and ΔE2000 as the color-reduction harness (45 frames, 960×540). Indicative only — re-run with `--clip` on a
lossless gameplay recording.

## Result: the claim does not hold; the useful part is already shipped

CRF 18 bitrate vs. unprocessed (negative = saves bits):

| Variant | clean pan | grain pan | static grain | gradients |
|---|---|---|---|---|
| **flatten 0.02 (shipped)** | −5.3% | **−56.1%** | **−64.3%** | −0.3% |
| Stage 1: palette 128 / 64 | +31 / +55% | +122 / +266% | +168 / +353% | +72 / +74% |
| Stage 1: RGB 5-bit grid | +21% | +69% | +88% | +29% |
| Stage 3: flat 3×3 merge | −2.7% | −49.7% | −58.6% | +0.3% |
| Stage 3: edge grid snap 2 px | −0.5% | +9.1% | +11.1% | −0.5% |
| Stage 3: merge + snap | −2.4% | −45.5% | −52.3% | +1.3% |
| Stage 4: nearest-neighbor 0.75× | +2.2% | +85% | +107% | −0.2% |
| Stage 4: nearest-neighbor 0.5× | +6.0% | +493% | +571% | +1.5% |
| Temporal dither (Bayer, shifted per frame) | +422% | +103% | +155% | +952% |
| Full pipeline, "50%" settings (pal 128 + merge + snap + dither) | +69% | +12% | +21% | +159% |
| Full pipeline, "100%" settings (pal 64 + merge + snap + dither + 0.75×) | +408% | +270% | +346% | +634% |
| flatten + merge | −7.4% | −57.7% | −66.4% | −0.8% |

At CRF 23 (typical quality) nothing except flatten stays negative on every clip; the full pipeline is +18% to +300%.

## Why

- **Quantization and dither add bits.** Palette/grid quantization creates contour edges and removes the smooth
  gradient the encoder predicts well; this matches the earlier posterize / k-means / Bayer results.
- **Temporal dither is the worst single stage.** Changing the pattern every frame turns static flat areas into
  fresh noise each frame, which is exactly what inter prediction cannot reuse (+100% to +950%).
- **Nearest-neighbor resample at a non-integer ratio (0.75×)** makes uneven 1-or-2 pixel duplication — new
  aliased edges on every texture, and they move on pans.
- **Grid-snapping edges** moves hard edges by up to a pixel and breaks the smooth motion the encoder tracks.
- **The only helpful piece is flat-area merge** (3×3 median where the Sobel edge strength is low). It removes
  the same noise edge-preserving flatten removes, but is slightly worse than flatten on every clip and adds
  nothing when combined (−57.7% vs −56.1% on grain is within noise of the model, at higher ΔE).
- The write-up's performance numbers (10–14 ms at 1080p60, "−20–35% bitrate") are unmeasured; none of them
  reproduce here.

## Decision

Not shipped as a separate plugin. Flat-area merge is already covered by Perceptual Color Reduction's edge-preserving
flatten, which is cheaper and better on every clip. Re-open only if a real lossless recording shows the opposite
(pixel-art or heavily upscaled content might behave differently: run `--clip` on it).
