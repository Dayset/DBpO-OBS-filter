# Atlas measurements (issue #4, first pass)

Six-second cuts of the 2560x1440 AV1 captures. Each variant encoded at 6000 kbps (libx264 veryfast, 60 fps). Scores are PSNR in dB against the source resampled to the same size and fps. Unsquish is bilinear. Fast clip = Planetside2AverageSpinLeft (v1.0.0), slow clip = Planetside2SlowestSpinLeft (v2.0.0).

## Fast spin, minimap region (x 0.01-0.21, y 0.61-0.98)

| Size | Variant | Whole frame | Fovea | Minimap |
|---|---|---|---|---|
| 1080p | Baseline | 23.27 | 21.46 | 21.36 |
| 1080p | Squish + atlas (side cutout) | 23.99 | 23.64 | 22.04 |
| 720p | Baseline | 25.54 | 23.91 | 24.18 |
| 720p | Squish + atlas (side cutout) | 25.18 | 25.37 | 23.58 |

Minimap slot scale: 0.95 at both sizes (floor 0.852), so the minimap is kept at about 95% of stream scale.

## Fast spin, bottom-right region (x 0.8-1.0, y 0.85-1.0), placeholder

| Size | Variant | Whole frame | Fovea | Region |
|---|---|---|---|---|
| 1080p | Baseline | 23.27 | 21.46 | 25.73 |
| 1080p | Squish only | 23.72 | 23.45 | 24.19 |
| 1080p | Squish + atlas | 24.13 | 23.77 | 26.94 |
| 720p | Baseline | 25.54 | 23.91 | 27.81 |
| 720p | Squish only | 25.25 | 25.59 | 25.45 |
| 720p | Squish + atlas | 25.19 | 25.45 | 27.66 |

## Slow spin, bottom-right region (placeholder)

| Size | Variant | Whole frame | Fovea | Region |
|---|---|---|---|---|
| 1080p | Baseline | 26.05 | 25.56 | 28.71 |
| 1080p | Squish only | 24.28 | 26.63 | 24.78 |
| 1080p | Squish + atlas | 24.22 | 26.13 | 27.44 |
| 720p | Baseline | 26.17 | 25.98 | 28.71 |
| 720p | Squish only | 23.80 | 26.22 | 24.18 |
| 720p | Squish + atlas | 23.65 | 25.76 | 26.56 |

## Open
- Minimap region on the slow clip (not yet measured).
- Option B (extra strip, non-standard output size) not tested.
- Noise/flicker metric not measured.
