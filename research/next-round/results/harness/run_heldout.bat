@echo off
setlocal enabledelayedexpansion
cd /d c:\dbpo-lab
echo HELD-OUT RUN. Settings come from the tuning clips only (research\next-round\HELDOUT.md). Run once, do not re-tune afterwards. Resumable.
set QP_MODE=
set CODEC=
set NO_VMAF=
set MIN_SECS=10
set KEYINT_S=2
set VBOXES=0.3,0.5,1.0
set MODELS=vmaf
set SIZES=1280x720,960x540,640x360
set CENTRES=0.05,0.1,0.2,0.3,0.5
set ROI=1
set ROI_CENTRES=0.1,0.3,0.5
set ROI_Q=-0.2,-0.4,-0.6
set ROI_SQ_Q=-0.2,-0.4
set ROI_CODECS=x264
set ARMS_ONLY=
set REFS=
for %%F in ("c:\dbpo-lab\out\Planetside_2_2026_11_15___21_39_25_09\ref.mkv" "c:\dbpo-lab\out\X264\ref.mkv" "c:\dbpo-lab\game2_ref.mkv") do (
  if exist %%F (set REFS=!REFS! %%F) else (echo MISSING held-out clip %%F - skipped. For game2 run prep_game2.bat first.)
)
python research\next-round\select_heldout.py c:\dbpo-lab\t2\results.jsonl c:\dbpo-lab\heldout_plan.json c:\dbpo-lab\heldout_env.bat
call c:\dbpo-lab\heldout_env.bat
echo [1/3] x264 at 1500, 3000, 6000
set CODECS=x264
set RATES=1500,3000,6000
python -u research\next-round\grid_t2.py c:\dbpo-lab\t2_heldout !REFS!
echo [2/3] hevc_nvenc at 3000, 6000
set CODECS=hevc_nvenc
set RATES=3000,6000
python -u research\next-round\grid_t2.py c:\dbpo-lab\t2_heldout !REFS!
echo [3/3] summary
python research\next-round\t2_summary.py c:\dbpo-lab\t2_heldout\results.jsonl > c:\dbpo-lab\t2_heldout\summary_heldout.txt
type c:\dbpo-lab\t2_heldout\summary_heldout.txt
pause