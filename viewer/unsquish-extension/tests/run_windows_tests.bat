@echo off
rem Browser tests for the viewer unsquish extension, for Windows. Uses the repo this file lives in.
rem Needs: git, python, ffmpeg on PATH. Browser comes from playwright (installed below).
cd /d "%~dp0..\..\.."
git pull --ff-only
if errorlevel 1 goto fail
python -m pip install --quiet playwright numpy pillow
if errorlevel 1 goto fail
python -m playwright install chromium
if errorlevel 1 goto fail

echo === Browser tests, viewer\unsquish-extension\tests (e2e.py skipped: needs scikit-image, which does not build on Python 3.15) ===
for %%F in (viewer\unsquish-extension\tests\e2e*.py) do (
  if /I not "%%~nxF"=="e2e.py" (
    echo --- %%F
    python "%%F" || echo FAILED: %%F
  )
)
echo Done. Send back the output above (the lines that say FAILED, or the last 30 lines of any failing test).
pause
exit /b 0

:fail
echo A setup step failed. Check that git, python and ffmpeg are installed, then run this file again.
pause
exit /b 1
