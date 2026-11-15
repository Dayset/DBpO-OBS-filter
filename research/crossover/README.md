# Squish vs plain stream across bitrates, same clip and frames (issue #33: E1, G4, G1)

Question: two real-footage runs disagreed on the sign of the squish effect at 6000 kbit/s (`research/restore-real`: squish 1.21 dB below plain; `research/foveation/real_footage_findings.md`: squish 1.24 dB above). Which is right?

Answer: both. They were run at different output sizes, so at very different bits per pixel. `restore-real` streams a 960x540 picture, where 6000 kbit/s is the load of about 24 Mbit/s at 1080p. The other run streams 1080p, where 6000 kbit/s is starved. This sweep reproduces both numbers on the same frames and shows where the sign changes.

## Setup
- PlanetSide 2 release v3.0.0 (sha256 `f52627aed49a9e7f`), `fps=60,scale=W:H:flags=area`. Metrics on frames 300-599, every 2nd frame (150 frames), luma PSNR and SSIM. Centre = the 30% x 30% box that the squish keeps 1:1.
- x264 veryfast CBR, bufsize 2x rate, GOP 120. Squish: `squish_math.Axis(c=0.15, linear)`, viewer unsquish bilinear. No restorer.
- 540p: all 906 frames encoded. 1080p: frames 240-599 encoded (1 s warm-up before the measured frames).
- Raw: `crossover_540p.json`, `crossover_1080p.json` (with the bitrate each arm actually produced). Script: `crossover_bench.py` (resumes from the JSON).

## Results (PSNR dB / SSIM / **centre PSNR** / centre SSIM)

960x540 output:

| CBR kbit/s | plain 960x540 | squish 720x404 (56% of pixels) |
|---|---|---|
| 750 | 19.59 / 0.446 / **18.70** / 0.329 | 20.66 / 0.506 / **20.15** / 0.414 |
| 1000 | 20.71 / 0.500 / **19.52** / 0.377 | 21.51 / 0.547 / **21.18** / 0.490 |
| 1500 | 21.65 / 0.547 / **20.28** / 0.423 | 23.23 / 0.641 / **23.30** / 0.643 |
| 2000 | 22.73 / 0.606 / **21.24** / 0.493 | 24.30 / 0.701 / **24.76** / 0.730 |
| 3000 | 24.97 / 0.720 / **23.32** / 0.643 | 25.62 / 0.767 / **26.80** / 0.822 |
| 4500 | 27.11 / 0.808 / **25.42** / 0.763 | 26.70 / 0.815 / **28.79** / 0.882 |
| 6000 | 28.59 / 0.854 / **26.90** / 0.825 | 27.35 / 0.841 / **30.26** / 0.913 |
| 9000 | 30.69 / 0.902 / **29.02** / 0.887 | 28.10 / 0.870 / **32.47** / 0.945 |

1920x1080 output:

| CBR kbit/s | plain 1920x1080 | squish 1440x810 (56%) | squish 1712x964 (80%) |
|---|---|---|---|
| 2000 | 19.11 / 0.514 / **17.91** / 0.356 | 20.35 / 0.573 / **19.98** / 0.453 | 19.57 / 0.537 / **18.74** / 0.396 |
| 3000 | 20.28 / 0.555 / **19.35** / 0.423 | 21.80 / 0.627 / **21.23** / 0.517 | 21.35 / 0.602 / **20.38** / 0.472 |
| 6000 | 23.52 / 0.682 / **21.71** / 0.544 | 25.41 / 0.763 / **25.27** / 0.738 | 24.62 / 0.727 / **23.18** / 0.629 |
| 12000 | 28.05 / 0.836 / **25.96** / 0.768 | 28.13 / 0.855 / **29.24** / 0.877 | 28.41 / 0.852 / **27.54** / 0.827 |
| 24000 | 31.97 / 0.919 / **29.96** / 0.894 | 29.96 / 0.906 / **33.02** / 0.942 | 31.18 / 0.916 / **31.47** / 0.921 |

## Reading
- **The conflict is resolved.** 1080p at 6000: plain 23.52 dB, squish 964p 24.62 dB (+1.09; the earlier run had 23.38 and 24.62). 540p at 6000: plain 28.60, squish 27.35 (-1.25; the earlier run had 28.54 and 27.33). Both earlier numbers reproduce.
- **Whole-frame gain depends on starvation.** The squish wins on whole-frame PSNR up to about 3000 kbit/s at 540p and about 12000 kbit/s at 1080p (the same bits per pixel), and loses above that. At Twitch's 6000 kbit/s 1080p60 it wins by 1.1 dB (964p) to 1.9 dB (810p) on this clip.
- **The centre gains at every bitrate tested**, including where the whole frame loses: +1.5 to +3.6 dB at 56% of the pixels, +0.8 to +1.6 dB at 80%.
- **Equal-quality bitrate (plain curve interpolated on log bitrate):** at 56% of the pixels the squish reaches the plain stream's centre quality with 43-50% less bitrate, and its whole-frame PSNR with 24-34% less in the starved range. At 80% of the pixels: 20-26% (centre) and 14-21% (whole frame) at 2000-6000 kbit/s.
- 2000 kbit/s at 1080p (G1): plain 19.11 dB, squish 810p 20.35, squish 964p 19.57.

Limits: one game, one 5 s spin, x264 only, PSNR/SSIM only (no VMAF build here), no restorer arm. Every arm overshoots its CBR target by 4-13%, by nearly the same amount within a row; at 1080p 6000 plain got about 2-3% more bits than the squish arms.
