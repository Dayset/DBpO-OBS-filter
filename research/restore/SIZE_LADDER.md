# Size ladder: quality vs parameters and energy (issue #25)

Test set: PS2 timeline frames 300–599 (every 5th frame, 60 frames), level 540 (960×540 stream → 1920×1080), x264 veryfast CBR 3000 and 6000 kbit/s. Luma, the same metrics as #23. Every size uses the same recipe: from scratch, 2000 steps, lr 2e-3, trained on the PS2 train range only (frames 0–299). Results in `results_ps2.json` under `ladder*/`, `int8_*/`, `tern_*/`.

## Table: size × format

| Size | Format | Width C | Params | MACs / source px | PSNR 3000 / 6000 | SSIM 3000 / 6000 | Edge sharp 3000 / 6000 | Power, est. (W) |
|---|---|---|---|---|---|---|---|---|
| 5k | float | 13 | 4,848 | 4,797 | 27.60 / 29.33 | 0.8277 / 0.8828 | 0.651 / 0.768 | ~0.2 |
| 5k | int8 | 13 | 4,848 | 4,797 | 27.59 / 29.32 | 0.8274 / 0.8825 | 0.648 / 0.766 | ~0.2 |
| 5k | ternary | 13 | 4,848 | 4,797 | 27.17 / 28.79 | 0.8226 / 0.8769 | 0.628 / 0.739 | ~0.2 |
| 10k | float | 20 | 9,972 | 9,900 | 27.75 / 29.51 | 0.8297 / 0.8849 | 0.661 / 0.781 | ~0.35 |
| 10k | int8 | 20 | 9,972 | 9,900 | 27.75 / 29.51 | 0.8296 / 0.8849 | 0.661 / 0.780 | ~0.35 |
| 10k | ternary | 20 | 9,972 | 9,900 | 27.41 / 29.09 | 0.8251 / 0.8798 | 0.632 / 0.746 | ~0.35 |
| 50k | float | 49 | 49,992 | 49,833 | 27.90 / 29.70 | 0.8322 / 0.8874 | 0.666 / 0.787 | ~1.1 |
| 50k | int8 | 49 | 49,992 | 49,833 | 27.90 / 29.70 | 0.8322 / 0.8874 | 0.667 / 0.788 | ~1.1 |
| 50k | ternary | 49 | 49,992 | 49,833 | 27.71 / 29.46 | 0.8291 / 0.8843 | 0.660 / 0.780 | ~1.1 |
| 200k | float | 102 | 201,360 | 201,042 | 27.93 / 29.73 | 0.8329 / 0.8881 | 0.669 / 0.791 | ~3 |
| 200k | int8 | 102 | 201,360 | 201,042 | 27.93 / 29.73 | 0.8330 / 0.8882 | 0.671 / 0.792 | ~3 |
| 200k | ternary | 102 | 201,360 | 201,042 | 27.78 / 29.55 | 0.8305 / 0.8856 | 0.659 / 0.779 | ~3 |

Power is an **ESTIMATE**, not measured: the 10k and 200k points are the midpoints of the findings-doc estimates (10k 0.2–0.5 W, 200k int8 2–4 W, `lowres-edge-restoration-findings.md`); 5k and 50k are interpolated on a log scale in MACs between those two points. #26 device numbers will replace this column.

## Curves

- **Quality vs parameters (float):** PSNR at 3000 / 6000: 5k 27.60 / 29.33, 10k 27.75 / 29.51, 50k 27.90 / 29.70, 200k 27.93 / 29.73. The gain flattens fast after 10k.
- **Per format:** int8 costs 0.00–0.01 dB at every size (per-tensor scale, worst weight error 1/256 of the tensor max). Ternary costs 0.15–0.54 dB, and the gap shrinks with size: about −0.4 to −0.5 dB at 5k and 10k, −0.2 dB at 50k, −0.15 dB at 200k.
- **Quality vs energy (estimate):** 5k → 10k: +0.15 dB for roughly +0.15 W. 10k → 50k: +0.15 to +0.19 dB for roughly +0.75 W. 50k → 200k: +0.03 dB for roughly +2 W.

## Recommendation

**Default: 10k, int8.** It keeps the full float quality (0.00 dB lost), the file is about 11 KB, and it is the size the findings doc's power budget allows with margin.

- **Next size up (50k): worth it only if #26 shows headroom.** It gains about +0.17 dB PSNR and +0.003 SSIM at about 5× the MACs. If the device run puts 10k well under the frame budget, 50k is the next step; otherwise not.
- **200k: not worth it.** +0.03 dB over 50k for about 4× the MACs and the largest training cost below.
- **Ternary: not the default.** It saves arithmetic but costs 0.4 dB at the default size, and the findings doc says it does not reduce memory traffic.

## 200k training cost

Measured here: CPU sandbox, 2 threads, 2000 steps on 60-frame shards. About 46 s per 400 steps, so about 4 min for the whole run (`train_float.py --config configs/ladder_200k.json`). Those steps are a small fraction of one corpus epoch.

Estimate for a full epoch, from `lowres-edge-restoration-findings.md` (RTX 5080, assumed 50 h corpus): about 110 GPU hours and about 40 kWh per epoch for 200k, against about 5–6 h and 2 kWh for 10k. These are not measured; they are the findings-doc numbers.

## Notes

- 50k and 200k were trained and evaluated in the sandbox (CPU, with the torch convolutions in `ps2_bench.py --fast`, checked against the numpy reference to 1.2e-7). The epic routed them to the PC, but the sandbox was fast enough.
- Width is the only axis that changed. Depth and kernels are fixed, so the receptive field matches at every size.
- Single source class (PS2) and one level (540). The 360 level and the other classes are not in this table.
