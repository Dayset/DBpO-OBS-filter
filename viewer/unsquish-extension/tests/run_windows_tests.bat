@echo off
rem Browser tests for the viewer unsquish extension, for Windows. Uses the repo this file lives in.
rem Needs: git and ffmpeg on PATH, and Python 3.12 (py launcher). Browser comes from playwright (installed below).
cd /d "%~dp0..\..\.."
git pull --ff-only
if errorlevel 1 goto fail
py -3.12 -m pip install --quiet playwright numpy pillow scikit-image
if errorlevel 1 goto fail
py -3.12 -m playwright install chromium
if errorlevel 1 goto fail

echo === Browser tests, viewer\unsquish-extension\tests (Python 3.12) ===
for %%F in (viewer\unsquish-extension\tests\e2e*.py) do (
  echo --- %%F
  py -3.12 "%%F" || echo FAILED: %%F
)
echo Done. Send back the output above (the lines that say FAILED, or the last 30 lines of any failing test).
pause
exit /b 0

:fail
echo A setup step failed. Check that git, Python 3.12 (py -3.12) and ffmpeg are installed, then run this file again.
pause
exit /b 1
