# T12 smooth-falloff ROI, CBR x264 (written 2026-11-18, before the run)

Purpose: the strongest version of the encoder-ROI rival. The narrow rule in ROUND4.md (T12) is the one applied here. This plan fixes how it is read, so it is written before any result.

Note: the capped-VBR run (`smooth_roi_capped_t12.bat`, `t2vbr`) was exploratory. This CBR run is the pre-registered one.

Clips (tuning, four): ref60, Planetside 21_29_34_08, PlanetsideDifferentSpeed, Planetside 21_26_27_05 (calm).

Arms, x264 CBR, 1500 and 3000 kbit/s, 60 fps, KEYINT_S 2, centre-50 and whole-frame VMAF:
- method: sq960x540_c30 and sq1280x720_c30. The method arm for a clip and rate is the one with the higher centre-50 score.
- hard ROI: roi_c50_q20, roi_c50_q40. The hard-ROI arm is the one with the higher centre-50 score.
- smooth ROI: sroi_c10_q40, sroi_c10_q60, sroi_c30_q40, sroi_c30_q60 (3 nested boxes, inner first). The smooth arm is the one with the higher centre-50 score.

Narrow rule, as written in the T12 entry: the claim narrows if the smooth arm comes within 2 VMAF of the method in centre-50 with equal or better whole-frame VMAF.

Reading applied (fixed before the run):
- A clip-rate cell counts as narrowing if smooth centre-50 >= method centre-50 - 2 AND smooth whole-frame >= method whole-frame (the method arm being the one chosen above).
- The narrow rule holds if at both 1500 and 3000 kbit/s at least 3 of the 4 clips count as narrowing.
- Otherwise the claim does not narrow on these clips.

Box-order check first: the nested boxes rely on x264 applying the first listed region where regions overlap. Checked before reading the narrowing result (see t12-smooth-roi-results.md).

Runner: t12_smooth_roi_cbr.bat (output c:\dbpo-lab\t12_smooth_cbr).
