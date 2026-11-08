@echo off
rem Short check (issue #18 + #19): busiest 4 s of every clip in sources\, 3000 and 6000 kbit/s, picture sizes,
rem squeeze and the interlace step, x264 and NVENC (NVENC is skipped if this PC has none). Resumable like run_sweep.bat.
call "%~dp0run_sweep.bat" --quick4 --out "%~dp0results_quick4" %*
