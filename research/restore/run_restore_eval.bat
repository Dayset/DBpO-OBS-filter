@echo off
rem Issue #23.5: matched CBR test on your own clips (x264 and NVENC P7, 3000 and 6000 kbit/s, 1080 / 540 / 360).
rem Put clips in sources\<class>\clip.mkv (same layout as research\sweep\sources; 1920x1080, 60 fps).
rem Double-click to start or resume; close the window at any time. Finished cells are kept in results_restore\results.json.
rem Optional:  run_restore_eval.bat --encoders x264 --seconds 10
setlocal
cd /d "%~dp0"

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo ffmpeg not found on PATH. Install it with:  winget install Gyan.FFmpeg
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python environment in .venv ...
  py -3 -m venv ".venv" 2>nul || python -m venv ".venv"
)
set "PY=.venv\Scripts\python.exe"
"%PY%" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo pip install failed.
  pause
  exit /b 1
)

if not exist "sources" (
  echo No sources\ folder here. Create sources\^<class^>\ and put clips in it, see the header of this file.
  pause
  exit /b 1
)

"%PY%" restore_eval.py --sources "%~dp0sources" --out "%~dp0results_restore" %*
echo.
echo Done or stopped. Upload results_restore\results.json. Run this file again to resume.
pause
