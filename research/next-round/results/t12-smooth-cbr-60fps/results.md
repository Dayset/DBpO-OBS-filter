# T12 smooth-falloff ROI, CBR x264, results (2026-11-18)

Scored under the reading in `plan.md`. Source: `results.jsonl` (the summary file does not list the smooth arms). Tuning clips, four.

## Box-order check
Smooth centre-30 against hard-ROI centre-30 at the same rate, for the chosen arms: 7 of 8 cells are above. One is below by 0.4 (ref60, 1500: smooth 26.2 vs hard 26.6). Not large enough to change the verdict, and that cell fails on whole frame anyway. Box order treated as OK.

## Per cell

| Clip | Rate | Method (c50 / whole) | Hard ROI (c50) | Smooth (c50 / whole) | Narrows? |
|---|---|---|---|---|---|
| ref60 | 1500 | sq960 27.19 / 17.66 | q40 25.68 | c10_q60 25.22 / 17.47 | No (whole) |
| PlanetsideDifferentSpeed | 1500 | sq960 37.12 / 20.36 | q40 28.82 | c10_q60 27.96 / 20.15 | No (c50) |
| Planetside 21_29_34_08 | 1500 | sq960 29.47 / 20.77 | q40 22.30 | c10_q60 22.22 / 19.39 | No (c50) |
| Planetside 21_26_27_05 | 1500 | sq960 78.15 / 44.42 | q40 83.84 | c30_q40 80.64 / 54.05 | Yes |
| ref60 | 3000 | sq960 46.98 / 27.81 | q40 31.09 | c30_q60 31.11 / 25.68 | No (c50) |
| PlanetsideDifferentSpeed | 3000 | sq960 51.01 / 27.76 | q40 50.41 | c30_q60 47.67 / 29.62 | No (c50) |
| Planetside 21_29_34_08 | 3000 | sq960 42.29 / 27.29 | q40 30.17 | c10_q60 29.58 / 24.55 | No (c50) |
| Planetside 21_26_27_05 | 3000 | sq1280 84.45 / 67.69 | q40 91.30 | c30_q60 89.39 / 60.19 | No (whole) |

## Verdict
- 1500 kbit/s: 1 of 4 clips narrow. Needs 3. Fails.
- 3000 kbit/s: 0 of 4 clips narrow. Fails.
- The pre-registered rule does not narrow the claim on these clips.

The smooth-falloff ROI gets closer to the method than the hard ROI in centre-50 on most clips, but gives up whole-frame VMAF to do it.

The capped-VBR run (`t2vbr`) was exploratory and is not used here. The G2 PASS lines in the summary compare the method with downscale; they are not the T12 test.
