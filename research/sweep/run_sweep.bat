@echo off
rem Issue #18 sweep. Double-click to start or resume; Ctrl+C or close the window to stop at any time.
rem Extra arguments go straight to dbpo_sweep.py auto, e.g.:  run_sweep.bat --quick
setlocal
cd /d "%~dp0"

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo ffmpeg not found on PATH. Install it with:  winget install Gyan.FFmpeg
  echo then open a new window and run this again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python environment in .venv ...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo Python 3.10+ not found. Install it with:  winget install Python.Python.3.12
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo pip install failed.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" dbpo_sweep.py auto %*
echo.
echo Results: %~dp0results   (results.csv, best.csv, summary.md, manifest.json)
pause
