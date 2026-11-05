# Real footage: squish and sharp regions vs plain 1080p60 (PlanetSide 2 clip, release v3.0.0)

Clip: 2 s cut from the 2560x1440 AV1 240 fps 400 Mbit/s release, 5 s in, brought to 1080p60 (every 4th frame, area downscale, lossless).
Each variant: x264 veryfast CBR 6000 kbit/s, 60 fps, viewer unfolds it back to 1080p; metrics vs the 1080p reference, frames 30-119.
Frame size 1712x964 (963 rows would be odd and 4:2:0 cannot encode that). Script: `real_footage_bench.py` (stages cache their output, so a killed run just resumes).

| Variant | Mbit/s | PSNR dB | SSIM | PSNR region 1 / 2 | Noise (error flicker, lower is better) |
|---|---|---|---|---|---|
| Baseline 1080p | 7.03 | 23.38 | 0.680 | 25.21 / 20.79 | 15.06 |
| Squish 964p | 6.78 | 24.62 | 0.734 | 24.38 / 21.62 | 12.73 |
| Squish 964p + 2 sharp regions | 6.79 | 25.07 | 0.751 | 27.21 / 22.99 | 12.04 |
| Squish 964p at 3x bitrate | 19.79 | 29.39 | 0.886 | 28.84 / 25.66 | 6.95 |

Caveats: one short, very busy clip; absolute dB are low because of that. The two regions are the test-config rectangles (bottom-right 20x15%, left-bottom 20x37%), not tuned to this game's HUD. Baseline got ~4% more bits than the squished variants (CBR overshoot), so it is not disadvantaged.
