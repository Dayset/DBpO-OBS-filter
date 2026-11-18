# Held-out run: selection rule and pass rules (written 2026-11-17, before any held-out result)

Clips: the held-out rows of `CLIPS.md` that exist on the PC (..._09, X264, game2_ref). Each is run once. If a setting or rule is changed after seeing a held-out number, the run is void and reported as such.

## Selection (from tuning clips only: `select_heldout.py`)
Per codec and rate (x264 1500, 3000, 6000; hevc_nvenc 3000, 6000), over the four tuning clips, cells within 10% of the target rate:
- gap_centre = centre-50% VMAF of an arm minus the best downscale rung on the same clip (best by centre-50%); gap_whole = the same for whole-frame VMAF.
- An arm is eligible if it has valid cells on all four tuning clips; its score is the median over clips.
- `method_balanced`: eligible sq arm with the largest median (gap_centre + gap_whole). `method_centre`: largest median gap_centre.
- `roi_balanced` (x264 only): same rule over roi_ arms. `method_roi_balanced`: same over sq..._roiNN arms.
Held-out arms: plain, every downscale rung (1280x720, 960x540, 640x360), and the chosen arms. The downscale arm is the best rung per clip, picked after the fact, which favours the downscale.

## Pass rules (all on held-out clips, each clip counted once; game2 reported separately because it is a different game)
- H1 x264: at 1500, 3000 and 6000 kbit/s the `method_balanced` arm beats the best downscale rung in the centre-50% box by 5 VMAF or more on at least 2 of the 3 clips.
- H2 HEVC NVENC: the same at 3000 and 6000.
- H3 ROI (x264): at 1500 and 3000 the `method_balanced` arm beats the `roi_balanced` arm by 3 VMAF or more in the centre-50% box, on at least 2 of 3 clips, counting a clip only if the ROI arm's whole-frame VMAF is within 1 of the method's or lower.
- Whole-frame gaps are reported for every cell and carry no pass or fail. Failing a rule is reported as a result.
