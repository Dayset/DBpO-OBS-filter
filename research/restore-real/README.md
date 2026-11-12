# Edge restoration after Foveated Squish, real gameplay (ternary 10k)

Question: after the outer areas are squished and blurred and then sent over the stream, does a small learned restorer trained on **real gameplay** recover the outer edges? The whole-picture restorer is one model for every kind of content, so this test uses one game picture and one small model, with no UI pixels.

## Setup
- **Footage:** PlanetSide 2 release clip `Planetside2LeftSpin.mp4` (v3.0.0; sha256 starts `f52627aed49a9e7f`), 2560×1440 240 fps, decoded to 960×540 at 60 fps with `ffmpeg -vf "fps=60,scale=960:540:flags=area"`: 906 frames (15.1 s).
- **Split:** train on frames 0–299 and 600–905. Test on the middle 5–10 s (frames 300–600) with four 2 s windows (120 frames) starting at uniform random frames, seed 0: 5.8 s, 6.5 s, 6.9 s, 7.6 s.
- **Degradation (viewer path of Foveated Squish):** `squish_math.Axis` with r = 0.75 in X and 404/540 in Y, centre c = 0.15 (30% sharp), linear curve. Squish to 720×404, x264 veryfast CBR, decode, unsquish bilinear. The plain arm streams 960×540 with the same CBR.
- **Bitrate:** 1500 kbit/s for both arms (6 Mbit/s at 1080p scaled to 960×540). Measured: squish 1573 kbit/s, plain 1568 kbit/s.
- **Restorer:** ternary (BitNet-style, straight-through) 3-conv residual net with 22 channels, **9,969 parameters**, trained for 1500 steps (16 random 64×64 crops per step, Adam, cosine). Loss L1 + 2×L1 on Sobel, as in #20. Float variant not trained, per the brief.
- **Arms:** `plain_1500k` (no squish), `sq_bilinear` (squish + viewer unsquish), `sq_unsharp1.0`, `sq_ternary10k`.
- **Metrics:** luma PSNR, luma SSIM, outer PSNR (outside the 30% centre box), outer edge sharpness (gradient magnitude at the top 10% GT edge pixels in the outer area, relative to GT; 1.0 = original).

## Results (busiest window: 7.6 s, frames 453–572)

| Arm | PSNR dB | SSIM | Outer PSNR dB | Outer edge sharpness |
|---|---|---|---|---|
| plain 1500 kbit/s (no squish) | 21.65 | 0.546 | 21.82 | 0.312 |
| squish, bilinear unsquish | 23.25 | 0.644 | 23.24 | 0.398 |
| squish + unsharp 1.0 | 22.79 | 0.639 | 22.87 | **0.551** |
| squish + ternary 10k restorer | **23.39** | **0.658** | **23.40** | 0.480 |

All four windows (raw in `results.json`):

| Window | Start | Restorer vs bilinear, PSNR | SSIM | Outer edge sharpness (bilinear → restorer) |
|---|---|---|---|---|
| 0 | 5.8 s | +0.14 dB | +0.014 | 0.399 → 0.480 |
| 1 | 6.5 s | +0.15 dB | +0.015 | 0.395 → 0.477 |
| 2 | 6.9 s | +0.15 dB | +0.015 | 0.398 → 0.480 |
| 3 | 7.6 s (busiest) | +0.15 dB | +0.014 | 0.398 → 0.480 |

Reading:
- **Squish works in this footage:** at the same bitrate, squish + bilinear unsquish beats the plain stream by about 1.6 dB PSNR and 0.10 SSIM overall, and the outer edges are about 27% sharper.
- **The ternary 10k restorer adds a small but consistent gain.** +0.15 dB PSNR, +0.015 SSIM, and about 20% more outer edge sharpness, on every window. The gain is the same size across windows, so the result does not depend on the choice of window.
- **Unsharp gives more edge sharpness (+38%) but costs 0.46 dB PSNR.** The restorer is the better trade for fidelity; unsharp is the better trade for raw crispness.
- **Honest limit:** a 0.15 dB gain is small. The restorer is still a jack of all trades on one clip, and the model is underfit (loss ~0.09 after 1500 steps). The test uses a single 15 s clip and one game, so any gain measured on a longer or different clip could differ.

The "busiest" ranking is weak here: the four windows differ by only about 0.2 in mean luma change per frame (28.16–28.38), so the headline window is not much busier than the others.

## Reproduce
```
REF=/path/to/ref960.rgb        # 906 frames, 960x540 RGB, from the decode command above
B="python3 research/restore-real/real_restore.py"
export REF_RGB=$REF WORK=/path/to/work
$B prep  $REF $WORK          # squish, stream, unsquish, plain stream (about 1 min)
$B train $WORK 1500          # ternary 10k, resumable (about 80 s on 2 cores)
for i in 0 1 2 3; do $B eval $WORK $i; done   # about 1 min each
$B report $WORK
```
Raw outputs: `results.json` (windows, arms, parameters, motion scores) and `bitrate.json`.

## Minimap-only vs full-frame restorer (approved crop x 10-197, y 340-514)

Same split, same four windows, same squish arm; metrics inside the minimap box only (`minimap_restore.py`, raw in `minimap_results.json`). The minimap model is the same ternary 10k net (9,969 params), trained on box crops only.

| Arm | PSNR dB (windows 0-3) | SSIM | Edge sharpness |
|---|---|---|---|
| plain 1500 kbit/s | 19.67-19.71 | 0.292-0.294 | 0.265-0.267 |
| squish, bilinear | 21.38-21.41 | 0.425-0.427 | 0.315-0.318 |
| squish + unsharp 1.0 | 21.30-21.33 | 0.426 | 0.372-0.374 |
| squish + full-frame ternary | 21.37-21.40 | 0.430-0.432 | 0.339-0.341 |
| squish + minimap ternary | 21.27-21.31 | 0.432-0.435 | 0.370-0.373 |

Reading: the minimap model gets the edge sharpness of unsharp (+17% over bilinear), with better SSIM than either. It is about 0.08 dB below bilinear in PSNR, and the full-frame model is about even on PSNR. The minimap model is +0.03 edge sharpness over the full-frame model on every window. This is one clip and four windows; the minimap is translucent over the scene, which limits what a box-only model can recover.

## Unsharp on top of the minimap model (box, four windows, mean)

Sweep in `minimap_unsharp_sweep.json` (`minimap_restore.py sweep`, sigma 1.5).

| Arm | PSNR dB | SSIM | Edge sharpness |
|---|---|---|---|
| minimap ternary | 21.30 | 0.434 | 0.372 |
| + unsharp 0.4 | 21.18 | 0.430 | 0.406 |
| + unsharp 0.6 | 21.10 | 0.427 | 0.423 |
| + unsharp 0.8 | 21.01 | 0.424 | 0.440 |
| + unsharp 1.0 | 20.92 | 0.421 | 0.457 |

Reading: unsharp on top of the minimap model raises edge sharpness monotonically (+23% at 1.0) but lowers PSNR and SSIM at every amount. This does not match the full-frame result in the epic (#20), where unsharp stacked and PSNR rose. Keep the two cases separate; on the minimap, unsharp trades fidelity for crispness.

## Minimap model, longer training (6,000 steps, same data)

Same split and box, cosine schedule over 6,000 steps (`MM_TOTAL=6000 MM_CKPT=mm_long.pt`). Training loss stays flat (0.1167 at 1,500 steps, 0.1150 at 6,000), so the model has converged to what this clip can teach it. Raw per-window results: `mm_eval_<i>mm_long.json` (not committed; summary below).

| Arm (mean, four windows) | PSNR dB | SSIM | Edge sharpness |
|---|---|---|---|
| squish, bilinear | 21.39 | 0.426 | 0.317 |
| minimap ternary, 1,500 steps | 21.30 | 0.434 | 0.372 |
| minimap ternary, 6,000 steps | 21.27 | 0.423 | 0.394 |

Reading: longer training trades SSIM for edge sharpness and does not improve PSNR. At 6,000 steps the SSIM is below the bilinear baseline. More steps on this single clip do not buy a better model; the limit is the data (about 10 s of training footage from one game), not the step count. A full-scale minimap model needs more captures across facilities, lighting, and map zoom.

## Squish at 6000 kbit/s with ternary and unsharp 0.25 (four windows, full frame, mean)

`squish6000_eval.py`; raw in `sq6000_results.json`. The ternary model is the 1,500 kbit/s full-frame model, applied out of distribution. Plain stream is the 960×540 6000 kbit/s stream.

| Arm | PSNR dB | SSIM | Outer edge sharpness |
|---|---|---|---|
| plain 6000 kbit/s | 28.54 | 0.852 | 0.813 |
| squish 6000, bilinear | 27.33 | 0.840 | 0.641 |
| squish 6000 + ternary | 27.68 | 0.847 | 0.766 |
| squish 6000 + ternary + unsharp 0.25 | 27.38 | 0.846 | 0.864 |

Reading: at 6000 kbit/s the squish arm trails the plain stream on PSNR and SSIM. The squish advantage seen at 1,500 kbit/s does not carry to this budget. The ternary model still improves on bilinear (+0.35 dB, +0.007 SSIM, outer edges +0.12). Adding unsharp 0.25 gives back about 0.3 dB but sharpens outer edges past the plain stream's level. The 1,500 kbit/s model was not trained on 6,000 kbit/s output, so these numbers are an out-of-distribution check, not a trained result.
