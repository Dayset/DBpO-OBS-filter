# Interlace step (field-packed frames) — findings, issue #19

**Verdict: no-go for the starvation ladder.** At the same CBR, sending one field per frame never beat the
plain smaller picture the ladder already uses, and the classic "field" layout costs 3x the bits on static
content. Measured with `quick_bench.py` (busiest 4 s, x264 veryfast) and confirmed by the sweep's `--pack`
arms; the PC run (`research/sweep/run_quick4.bat`) repeats it with NVENC P7 on your clips.

## Setup

- Source: PlanetSide 2 release clip v3.0.0, busiest 4 s by the 240x135 temporal metric (starts at 0.32 s,
  CRF-23 demand ~33 Mbit/s), scaled to 1920x1080 at 60 fps.
- x264 veryfast, CBR (maxrate = bufsize = target), keyframe 2 s, 2 B-frames.
- Score: luma vs the 1080p60 source; centre = middle 30% x 30% (SSIM, PSNR); whole-frame SSIM every 4th frame;
  temporal error = mean |frame-to-frame change of the view - of the source| in the centre (lower is better).
- Arms:
  - `1080`, `720`, `540`: plain stream (720/540 = what the starvation ladder sends), viewer bilinear upscale.
  - `vhalf`: 1920x540 progressive (half height, full width, no interlacing) — the control for "is it the
    interlacing or just the pixel count".
  - `field`: one field per frame in the top half, parity alternating, black below (the #19 design).
  - `split`: even fields in the top half, odd in the bottom half; each frame replaces only its own half.
  - Viewers for field/split: `bwdif` (ffmpeg, one field of look-ahead), `bob` (edge-directed spatial only),
    `dbpo` (`fieldpack_math.Viewer`, no look-ahead, yadif-style clamp). `0`/`1` = anti-twitter off / [1 2 1].

## Results (centre SSIM / whole SSIM; best interlace viewer shown)

| arm | 3000 kbit/s | 6000 kbit/s | 15000 kbit/s (mild starvation) |
|---|---|---|---|
| 1080p | 0.462 / 0.601 | 0.557 / 0.704 | **0.808 / 0.867** |
| 720p | 0.500 / 0.664 | 0.648 / 0.766 | 0.796 / 0.864 |
| 540p | **0.549 / 0.703** | **0.665 / 0.781** | 0.763 / 0.849 |
| 1920x540 progressive | 0.490 / 0.654 | 0.614 / 0.745 | 0.772 / 0.849 |
| field, anti-twitter 1, bwdif | 0.484 / 0.646 | 0.606 / 0.735 | 0.763 / 0.840 |
| field, anti-twitter 0, bob | | 0.611 / 0.740 | |
| split, anti-twitter 1, bwdif | 0.472 / 0.626 | 0.555 / 0.702 | 0.725 / 0.816 |
| split, anti-twitter 0, bob | 0.475 / 0.636 | 0.562 / 0.707 | |

Viewer: `bwdif` and `bob` are within 0.006 of each other; the look-ahead-free `dbpo` clamp is 0.08 worse (its
temporal fill picks up stale rows in fast motion), so a shipped viewer would need one frame of delay anyway.

Static cost (one still frame for 2 s, CRF 23): 1080p 834 kbit/s, field 2535 (anti-twitter 1) / 5624 (off),
split 905. Alternating the parity in place is a half-line vertical jitter every frame, so the encoder loses
its free skip blocks; the split layout fixes that, but then each half is a 30 fps sequence whose motion is
twice as large, and it loses in motion.

## Why it does not pay

1. The field has the pixel count of 720p (1.04 M vs 0.92 M) but worse prediction: field and split both land
   between 1080p and 720p in busy scenes, and below 720p and 1080p in mild starvation.
2. Half height without interlacing (`vhalf`) scores at least as well as `field`: the alternating parity adds
   no measurable detail at these bitrates, only encoder cost.
3. The ladder's 720p → 540p → 360p steps already give the best centre at every starved bitrate measured here.

## What is in the repo

- `fieldpack_math.py`: packing (`pack`, `pack_split`), anti-twitter pre-filter, the reference viewer.
- `quick_bench.py`: this bench (`--seconds`, `--kbps`, `--arms`, `--encoder nvenc`), resumable.
- `research/sweep`: `--pack field,split` arms with a bwdif viewer, H7 in `summary.md`, `run_quick4.bat`.
- `viewer/fieldpack-extension/deinterlace.js`: WebGL prototype from the first cut (no look-ahead); kept as
  research, not wired into the extension.

## What would change the verdict

Only a PC result that contradicts this: H7 "supported" in the `run_quick4.bat` summary (NVENC P7 or a
source class where field-packed beats the best size). Then the OBS filter would get the step as a marker v1
code (13/14 = field + parity), and the extension the bwdif-style pass with one frame of delay.
