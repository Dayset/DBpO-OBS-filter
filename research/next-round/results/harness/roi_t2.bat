@echo off
cd /d c:\dbpo-lab
echo T12 ROI head to head. RUN THIS ONLY AFTER THE LEAN T2 RUN HAS FINISHED. Resumable, same results file.
set QP_MODE=
set CODEC=
set NO_VMAF=
set MIN_SECS=10
set KEYINT_S=2
set VBOXES=0.3,0.5,1.0
set MODELS=vmaf
set SIZES=1280x720,960x540,640x360
set CENTRES=0.05,0.1,0.2,0.3,0.5
set CODECS=x264
set RATES=500,1000,1500,3000,6000
set ROI=1
set ARMS_ONLY=roi
set ROI_CENTRES=0.1,0.3,0.5
set ROI_Q=-0.2,-0.4,-0.6
set ROI_SQ=sq960x540_c10,sq1280x720_c20,sq1280x720_c30
set ROI_SQ_Q=-0.2,-0.4
set ROI_CODECS=x264
set REFS=c:\dbpo-lab\ref60.mkv c:\dbpo-lab\out\Planetside_2_2026_11_15___21_29_34_08\ref.mkv c:\dbpo-lab\out\PlanetsideDifferentSpeed\ref.mkv c:\dbpo-lab\out\Planetside_2_2026_11_15___21_26_27_05\ref.mkv
python -u research\next-round\grid_t2.py c:\dbpo-lab\t2 %REFS%
python research\next-round\t2_summary.py c:\dbpo-lab\t2\results.jsonl > c:\dbpo-lab\t2\summary_roi.txt
type c:\dbpo-lab\t2\summary_roi.txt
pause