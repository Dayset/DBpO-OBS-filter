@echo off
rem Issue #24.5: restorer training on this PC. Double-click to start or resume; close the window at any time.
rem The run resumes from its last checkpoint (every 100 steps), so at most 100 steps are lost on close.
rem Use CUDA if the PyTorch install has it, CPU otherwise. Pick a config (default: real captures):
rem   run_train.bat                          -> configs\l540_real_float.json
rem   run_train.bat configs\smoke_cpu.json   -> any other config
setlocal
cd /d "%~dp0"

set "CFG=%~1"
if "%CFG%"=="" set "CFG=configs\l540_real_float.json"

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
  echo pip install failed. PyTorch with CUDA: see https://pytorch.org/get-started/locally/
  pause
  exit /b 1
)

"%PY%" train_float.py --config "%CFG%"
echo.
echo Training stopped. Run this file again to resume.
pause
