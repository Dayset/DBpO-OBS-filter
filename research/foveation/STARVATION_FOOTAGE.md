# Footage for the starvation work (#13, #17)

## The test timeline used for `starvation_findings.md` (#13)

It is made from the public release clip; nothing else is needed.

1. Download the source (755 MB, 2560x1440 AV1 240 fps, 15 s):
   `https://github.com/Dayset/DBpO-OBS-filter/releases/download/v3.0.0/Planetside2LeftSpin.mp4`
   (sha256 starts `f52627aed49a9e7f`).
2. Build the 10 s 1080p60 timeline (calm pan 3 s from the still at 1.0 s / real fast spin 4 s from 5.0 s /
   calm pan 3 s from the still at 13.0 s). About 30 s; writes `WORK/ref.u8` (3.7 GB raw RGB, 600 frames):

   ```
   python3 research/foveation/starvation_bench.py build Planetside2LeftSpin.mp4 WORK
   ```
3. Check it is the same timeline (sha256 of the raw RGB bytes, first 16 hex digits):

   | Segment | Frames | sha256 |
   |---|---|---|
   | calm1 | 0–179 | `1b4f680c0d7afd48` |
   | spin | 180–419 | `5382301be201755e` |
   | calm2 | 420–599 | `ba3d6eea9135920d` |
   | whole `ref.u8` | 0–599 | `ff6290c38509f1e6` |

   ```
   python3 -c "import numpy as np,hashlib;r=np.memmap('WORK/ref.u8',np.uint8,'r',shape=(600,1080,1920,3));[print(n,hashlib.sha256(r[a:b].tobytes()).hexdigest()[:16]) for n,a,b in [('calm1',0,180),('spin',180,420),('calm2',420,600),('all',0,600)]]"
   ```
   Made with ffmpeg 6.1.1 (dav1d), OpenCV 5.0.0, numpy 2.5. Another ffmpeg/OpenCV can change a few pixel values
   (the pans use `cv2.warpAffine`, the spin uses ffmpeg's area scaler). If only calm hashes differ, the results
   should still match to ~0.05 dB; say so in the issue instead of chasing the hash.
   A bit-exact lossless copy of the timeline also exists
   (`starvation_timeline_1080p60_rgb_lossless.mkv`, 970 MB, libx264rgb qp 0); if it gets attached to a release,
   `starvation_bench.py load THAT.mkv WORK` fills `ref.u8` from it instead of `build`.
4. Reproduce the findings (2 cores: ~1.5 min per variant, ~1 h for everything; needs ~8 GB free disk):

   ```
   B="python3 research/foveation/starvation_bench.py"
   $B detect WORK
   $B run WORK 6000 base 720_black 540_black 360_black true720 true540 true360 720_edge 540_edge 360_edge \
        540_mirror 540_edge_c 540_edge_nomark sq964 sq810 sq540
   $B run WORK 3000 base 720_black 540_black 360_black true540 true360 540_edge sq810 sq540
   $B dyn WORK 6000 calm1=1080,spin=540,calm2=1080
   $B dyn WORK 6000 calm1=1080,spin=540,calm2=1080 kf
   $B dyn WORK 6000 auto
   $B dyn WORK 3000 auto
   python3 research/foveation/starvation_report.py WORK 300   # tables + crop strips
   ```
   Numbers to hit (x264 is deterministic for the same build; other builds: within ~0.1 dB / 0.002 SSIM):
   - spin, 6000: `base` center PSNR 22.08, `540_black` 24.02, `true540` 24.06, `540_edge` 23.34
   - spin, 3000: `base` 19.51, `360_black` 22.34
   - `detect`: `corr_temporal` 0.997
   - `dyn auto` levels: 6000 → [1080, 540, 1080], 3000 → [1080, 360, 1080]; marker_ok 1.0 everywhere
   The bench now uses `starvation_math` for the marker; if a number moves, check that first.

## Real gameplay for #17: near-lossless is enough, not terabytes

Measured on the timeline above (x264 veryfast, 4:2:0, decoded and compared to the lossless frames, luma PSNR):

| Capture | Size | Error vs lossless (calm / spin / calm) |
|---|---|---|
| Lossless RGB (FFV1 / x264rgb qp 0) | ~780 Mbit/s → **58 GB per 10 min** | 0 |
| x264 CRF 12 | 54 Mbit/s → 4 GB per 10 min | 42.2 / 40.0 / 41.5 dB |
| x264 CRF 16 | 34 Mbit/s → 2.5 GB per 10 min | 40.7 / 37.9 / 40.3 dB |

- The starved-stream errors being studied are 19–26 dB in busy scenes, 15+ dB below CRF-12 capture noise, so
  capture noise is irrelevant there. In calm scenes the 1080p stream reaches 34–38 dB, close to the CRF-16
  floor, so use **CRF ≤ 12 (or NVENC/AV1 CQP ≈ 10–14)** and say which in the results.
- Best recipe (what the release clip was): record at the game's native **1440p** at a high bitrate (the clip is AV1
  ~400 Mbit/s, 240 fps); the bench's area downscale to 1080p averages out most capture artifacts. 60 fps is enough
  for the bench (it takes every 4th frame of 240 fps anyway).
- OBS: Settings → Output → Recording, Output mode Advanced, Recording format MKV, encoder NVENC AV1 or HEVC (or
  x264), rate control CQP 12 (x264: CRF 10, preset veryfast), keyframe interval default, same 1920x1080 (or
  2560x1440) canvas and 60 fps as the stream. 5–10 min ≈ 3–30 GB depending on the encoder.
- What to record: fights, fast turns, running through foliage, menus/map, respawn screens, idle standing; at least
  two games. Real recordings won't fit the fixed calm/spin/calm `SEGS`: #17 should add a mode that cuts any clip
  into 2 s windows and reports per-window best level, oracle vs `auto`, and the CRF-23 demand per window.
