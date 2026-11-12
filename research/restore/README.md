# Edge restorer research (epic #20)

- `SPEC.md`: shapes, ops, weight file format. Start here.
- `restore_math.py`: numpy reference forward pass and the RSTR v1 loader/exporter.
- `restore_golden.json`: fixed-seed golden output. Checked by the test below.

Test (issue #21.5):

    python3 research/restore/restore_math.py --check

Prints `OK` and exits 0 when the reference matches the golden file.

## Results (issue #22, repo clips)

Baseline is the repo clips (`bitrate_harness.clip_frames`, 2-frame shutter, x264 CRF 28), not the prototype's synthetic scene. See issue #29. Mean of 4 test clips (ids 0-3).

### Source → 960×540, CRF 28

| Arm | PSNR | SSIM | Edge sharp |
|---|---|---|---|
| 540 bicubic | 32.80 | 0.893 | 0.718 |
| 540 bicubic + unsharp 1.0 | 32.16 | 0.876 | 0.977 |
| 540 float CNN | 34.41 | 0.903 | 0.789 |
| 540 float CNN + unsharp 1.0 | 32.55 | 0.885 | 1.124 |
| 540 ternary CNN | 33.93 | 0.899 | 0.775 |
| 540 ternary CNN + unsharp 1.0 | 32.38 | 0.881 | 1.104 |
| 360 bicubic | 30.74 | 0.876 | 0.506 |
| 360 bicubic + unsharp 1.0 | 30.43 | 0.865 | 0.626 |
| 360 float CNN | 32.55 | 0.888 | 0.701 |
| 360 float CNN + unsharp 1.0 | 31.29 | 0.867 | 0.975 |
| 360 ternary CNN | 31.62 | 0.881 | 0.635 |
| 360 ternary CNN + unsharp 1.0 | 30.85 | 0.863 | 0.862 |

Unsharp sweep (0 / 0.4 / 0.6 / 0.8 / 1.0 / 1.2) for float and ternary is in `results.json`.

### Fixed stream bitrate (CBR, 960×540 output)

| Target | Arm | PSNR | SSIM | Edge sharp | Produced kbit/s |
|---|---|---|---|---|---|
| 250 | full 960×540 | 33.79 | 0.888 | 0.732 | 227 |
| 250 | shrink 540 + bicubic | 33.63 | 0.900 | 0.749 | 188 |
| 250 | shrink 540 + restore + unsharp 1.0 | 32.76 | 0.890 | 1.170 | 188 |
| 300 | full 960×540 | 34.33 | 0.894 | 0.758 | 239 |
| 300 | shrink 540 + bicubic | 33.93 | 0.903 | 0.764 | 214 |
| 300 | shrink 540 + restore + unsharp 1.0 | 32.91 | 0.892 | 1.187 | 214 |

Shrink arms produced fewer bits than the full arm at the same target (rate control), so the full arm is favoured on PSNR. See issue #22 comment for the full discussion.
