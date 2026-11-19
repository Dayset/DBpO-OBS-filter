# Next-round results (small files only)

Test clips and encoded videos are not in this folder. The JSON and text results record clip names and bitrates, but not clip checksums. If the clips are replaced, these results cannot be tied to the old files, so keep a checksum manifest before deleting anything.

| Folder | What it is | Verdict |
|---|---|---|
| `t12-smooth-cbr-60fps/` | T12 smooth-falloff ROI, CBR x264, 1500 and 3000 kbit/s, four tuning clips. Pre-registered in `t12-smooth-roi-plan.md`. | Does not narrow the claim (1 of 4 at 1500, 0 of 4 at 3000). See `t12-smooth-roi-results.md`. |
| `canvas-sweep-120-10s/` | 120 fps canvas sweep, 10 s tuning clip, 3000 and 6000 kbit/s, 19 canvases. | No squish canvas meets the whole-frame condition at either rate. |
| `canvas-sweep-60-10s/` | 60 fps canvas sweep, 10 s tuning clip (ref60_10s), 3000 and 6000 kbit/s. | Sweet spot 1344x756 at 3000 only. None at 6000. |
| `wpsnr-sigma-0.15-0.30-120fps/` | Weighted PSNR, σ 0.15 and 0.30, 120 fps run. | Near-centre-crop weights. Superseded by σ 0.50. Sensitivity only. |
| `wpsnr-sigma-0.50/` | Weighted PSNR, σ 0.50, 120 fps run. Chosen after seeing the other two, so exploratory. | Squish ahead of best downscale under gauss, behind under uniform. |
| `full120-leftspin/` | Run log for PS2LEFTSPIN120 (120 fps, high motion, held-out for that test). | Results file not included. Log only. |
| `harness/` | Scripts and inputs used by the runs (`grid_t2.py` from the PC, the .bat runners, held-out plan and clip draft). | `grid_t2.py` here is the PC copy with weighted PSNR. The untracked repo copy in `research/next-round/` is older. |
| `unlabeled/` | Result files whose run is not yet identified. | Owner to label before use. |

Held-out 60 fps run (`run_heldout.bat`, PS2run120): not run yet. No results.
