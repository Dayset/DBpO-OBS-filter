# dbpo-squish (task S1, #39)

C filter over yuv4mpeg 4:2:0 8-bit pipes, built on `plugins/foveated-squish/src/squish-math.c` (centre 30%, linear).

    make
    ffmpeg -i in.mp4 -f yuv4mpegpipe - | ./dbpo-squish 1440 810 | ffmpeg -i - ...      # area-filtered squish
    ./dbpo-squish -p 1440 810    # point-sampled      ./dbpo-squish -u 1920 1080   # bilinear unsquish      -t  prints ms per frame

`python3 test_vs_python.py in.y4m out.y4m 1920 1080 1440 810` compares the luma plane with the Python reference: mean
difference 0.04 of 255 (max 8, at the centre/periphery transition). `timing.py` writes `research/stack/module_timing.json`.

Result on the 2-core sandbox, one thread, 1080p to 1440x810: **area 4.5 ms, point 3.9 ms** against 16 ms saved in x264
veryfast. Done-when limit was 5 ms on the owner's PC; re-run `timing.py` there. Unsquish 6.7 ms. 1440p to 1080p: 8.4 / 6.7 ms.
Round trip squish then unsquish, luma PSNR 32.8 dB on the fast-spin clip.
