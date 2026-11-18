@echo off
cd /d c:\dbpo-lab
echo T12 capped-VBR run (ABR-like: average = target, peaks up to 1.5x). RUN AFTER smooth_roi_t12.bat. Own results folder t2vbr.
set QP_MODE=
set CODEC=
set NO_VMAF=
set ARMS_ONLY=
set ONLY_ARMS=sq960x540_c30,sq1280x720_c50,roi_c50_q20,roi_c50_q40,sroi_c10_q40,sroi_c10_q60,sroi_c30_q40,sroi_c30_q60
set CAP_VBR=1.5
set ROI=1
set ROI_CENTRES=0.5
set ROI_Q=-0.2,-0.4
set ROI_SQ=
set MIN_SECS=10
set KEYINT_S=2
set VBOXES=0.3,0.5,1.0
set MODELS=vmaf
set CODECS=x264
set RATES=500,1000,1500,3000,6000
set SMOOTH_ROI=1
set SROI_CENTRES=0.1,0.3
set SROI_Q=-0.4,-0.6
set SROI_STEPS=3
set ROI_CODECS=x264
set REFS=c:\dbpo-lab\ref60.mkv c:\dbpo-lab\out\Planetside_2_2026_11_15___21_29_34_08\ref.mkv c:\dbpo-lab\out\PlanetsideDifferentSpeed\ref.mkv c:\dbpo-lab\out\Planetside_2_2026_11_15___21_26_27_05\ref.mkv
python -u research\next-round\grid_t2.py c:\dbpo-lab\t2vbr %REFS%
python research\next-round\t2_summary.py c:\dbpo-lab\t2vbr\results.jsonl > c:\dbpo-lab\t2vbr\summary_capped.txt
type c:\dbpo-lab\t2vbr\summary_capped.txt
pause