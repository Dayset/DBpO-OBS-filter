# Bitrate starvation: lower the resolution inside a fixed 1080p frame (measured, Nov 2026)

Question: when a scene needs far more bits than the stream has, can the sender shrink the picture *inside* the
1920x1080 frame (no stream restart, no OBS/Twitch reconfiguration), tell the viewer, and have the viewer scale it
back up — and does it pay? Script: `starvation_bench.py`, tables + crops: `starvation_report.py`.

## Setup

- **Footage:** PlanetSide 2 release clip v3.0.0 (2560x1440 AV1, 240 fps), brought to 1080p60 lossless.
  Timeline (10 s): **calm1** 3 s slow pan (1.5 px/frame) over a still from 1.0 s, **spin** 4 s of the real fast
  spin from 5.0 s, **calm2** 3 s slow pan over a still from 13.0 s.
- **Demand** (x264 veryfast CRF 23, 1080p60): calm ~2.6–2.8 Mbit/s, spin **33.4 Mbit/s** = 5.6x the 6 Mbit/s budget.
- **Stream:** x264 veryfast CBR 6000 (and 3000 = uplink drop), 60 fps, GOP 2 s, 2 B-frames.
- **Sender:** picture downscaled (area) to 1280x720 / 960x540 / 640x360, placed top-left in the 1080p frame, rest
  padded. **Viewer:** crops the picture and upscales bilinear (what the extension does). Metrics vs the 1080p
  reference, luma: PSNR, PSNR in the center box (±15%), SSIM, SSIM in the center box. First 30 frames skipped.
- **Signal:** 5-bit code (4 bits level + parity, Manchester: each bit is a white/black pair of 16x16 cells) in a
  160x16 MB-aligned strip at the bottom-right (x 1760, y 1056).

## 1. Padding: black is free, edge-repeat costs bits, mirror is a disaster

Spin segment, 6000 kbit/s (center PSNR / center SSIM):

| Picture | black | edge-repeat | mirror | edge, centred | real low-res stream |
|---|---|---|---|---|---|
| 720p | 23.63 / 0.647 | 22.84 / 0.605 | 20.68 / 0.485 | 22.59 / 0.591 | 23.76 / 0.654 |
| 540p | **24.02 / 0.676** | 23.34 / 0.637 | 19.82 / 0.444 | 23.05 / 0.620 | 24.06 / 0.677 |
| 360p | 23.57 / 0.652 | 23.27 / 0.632 | 19.01 / 0.414 | 23.10 / 0.621 | 23.58 / 0.653 |
| 1080p (today) | 22.08 / 0.558 | | | | |

- **Black padding equals a real 540p/360p stream within 0.05 dB.** Flat black macroblocks are skipped (≈0 bits), so
  the picture gets the whole budget. No restart is needed to get the low-res benefit.
- **Edge-repeat is 0.3–0.8 dB worse than black.** The repeated stripes change every frame with the picture's border
  and the encoder pays to update them; mirror (a moving copy of the picture) is worse still (−4 dB).
- **Top-left beats centred** (fewer padding edges, MB-aligned). Same at 3000 kbit/s: 540 black 21.76 vs edge 20.38.
- The earlier finding "black bars lose half the gain" was about *squish in a smaller box*: there the box shrank the
  picture. Here shrinking is the point, and black is the cheapest filler.

## 2. When it pays: only when starved

| 6000 kbit/s, center PSNR / SSIM (whole frame) | calm1 | spin | calm2 |
|---|---|---|---|
| 1080p | **37.53 / 0.984** | 22.08 / 0.691 | **33.84 / 0.957** |
| 720 black | 28.93 / 0.937 | 23.63 / 0.758 | 26.41 / 0.919 |
| 540 black | 26.91 / 0.902 | **24.02 / 0.777** | 24.60 / 0.886 |
| 360 black | 24.29 / 0.826 | 23.57 / 0.766 | 22.22 / 0.807 |

| 3000 kbit/s | calm1 | spin | calm2 |
|---|---|---|---|
| 1080p | **34.03 / 0.969** | 19.51 / 0.569 | **29.11 / 0.891** |
| 720 black | 28.38 / 0.923 | 20.82 / 0.645 | 25.50 / 0.886 |
| 540 black | 26.64 / 0.891 | 21.76 / 0.682 | 24.17 / 0.866 |
| 360 black | 24.15 / 0.816 | **22.34 / 0.705** | 22.03 / 0.791 |

- In the spin, 1080p is blocky mush (see `img/starvation_crops_6000.jpg`); 540p is +1.9 dB center, SSIM 0.56 → 0.68.
  At 3000 kbit/s the best level drops to 360p (+2.8 dB center). **The right level moves with demand / budget.**
- In calm scenes a fixed low resolution throws away 6–10 dB. **A static setting can't work; it must be dynamic.**

## 3. Switching mid-stream: no penalty

Oracle schedule 1080 → 540 → 1080 at the segment cuts, 6000 kbit/s, no keyframe at the switch:

| | calm1 center / SSIM | spin | calm2 | whole clip SSIM |
|---|---|---|---|---|
| 1080p static | 37.53 / 0.984 | 22.08 / 0.691 | 33.84 / 0.957 | 0.852 |
| 540 static | 26.91 / 0.902 | 24.02 / 0.777 | 24.60 / 0.886 | 0.845 |
| **dynamic 1080-540-1080** | **37.51 / 0.983** | **24.02 / 0.778** | **35.46 / 0.976** | **0.894** |
| dynamic, keyframe forced at switch | 37.51 / 0.983 | 24.02 / 0.776 | 35.71 / 0.978 | 0.895 |
| dynamic, edge padding | 37.51 / 0.983 | 23.37 / 0.752 | 35.14 / 0.974 | 0.883 |

- **Every segment gets the best of both static choices; calm2 is even better than plain 1080p** (35.46 vs 33.84):
  after the spin, 1080p spends seconds recovering from the starved reference frames, the dynamic stream comes back
  clean.
- No bit spike or quality dip from the switch itself: the 10 frames after 1080→540 cost the same as in a static 540
  stream (3.37 vs 3.37 Mbit) at the same quality (25.0 vs 24.9 dB center).
- A forced keyframe at the switch buys ~1 dB for the first ~0.3 s after going back up, nothing else. OBS filters can't
  force keyframes, and they aren't needed.
- Whole-frame PSNR of the dynamic runs is lower in calm parts only because the 160x16 marker sits over the HUD corner
  at 1080p and counts as error; SSIM and the center numbers are unaffected.

## 4. Detecting starvation from inside the filter

Per frame at 240x135 luma (cheap): temporal = mean |change from the previous frame|, spatial = mean gradient.
Over 0.5 s windows, CRF-23 bits vs temporal: **r = 0.997**; spatial alone r = −0.96 (the spin is *blurrier*, so
spatial detail alone points the wrong way); temporal × spatial r = 0.998. On this game, CRF-23 demand at 1080p60 ≈
1.35 Mbit/s per unit of the temporal metric in both calm and spin.

**Auto controller** (`auto_schedule`): median of the last 3 metric values (a scene cut is a one-frame spike),
EMA ≈ 0.13 s, estimated demand / budget → level (<1.5 → 1080, <3 → 720, <6 → 540, else 360); drop a level after 6
frames of agreement, raise after 60.

| | calm1 center / SSIM | spin | calm2 | clip SSIM | levels chosen |
|---|---|---|---|---|---|
| 6000, 1080p static | 37.53 / 0.984 | 22.08 / 0.691 | 33.84 / 0.957 | 0.852 | |
| 6000, auto | 37.51 / 0.983 | 24.00 / 0.772 | 31.95 / 0.939 | **0.880** | 1080 → 540 (frame 192) → 1080 (494) |
| 3000, 1080p static | 34.03 / 0.969 | 19.51 / 0.569 | 29.11 / 0.891 | 0.776 | |
| 3000, auto | 34.12 / 0.968 | 21.91 / 0.688 | 28.72 / 0.889 | **0.825** | 1080 → 360 (192) → 1080 (503) |

- It picks the level the oracle would (540 at 6000, 360 at 3000) on its own. The 12 frames of 1080p before it
  reacts drain the encoder buffer, so the first ~10 frames at the new level are starved; dropping faster than 6
  frames would help.
- The slow way up (1 s) costs calm2 ~2–4 dB for ~1.2 s. That's the price of not flapping; tune on real sessions.
- Calibration (1.35 Mbit/s per unit) comes from one game and one clip. It needs real sessions, other games, and a
  second input: the stream's own congestion / dropped frames, for uplink drops the content metric can't see.

## 5. Squish together with a lower resolution

| Spin, center PSNR / center SSIM | 6000 | 3000 |
|---|---|---|
| 1080p | 22.08 / 0.558 | 19.51 / 0.436 |
| 540 black | 24.02 / 0.676 | 21.76 / 0.542 |
| 360 black | 23.57 / 0.652 | **22.34 / 0.571** |
| Squish 964 (shipped v3, own frame size) | 23.99 / 0.665 | |
| Squish into 1440x810 + edge pad | 24.46 / 0.690 | 21.02 / 0.502 |
| Squish into 960x540 + edge pad | **24.96 / 0.726** | 21.43 / 0.526 |

- At 6000, squishing the low-res picture gives the sharpest center of anything tested (SSIM 0.726 vs 0.676). Note
  these used edge padding, ~0.4–0.7 dB worse than black — black should add more.
- At 3000, squish into 960x540 is still too big: a squish level at 640x360 (or smaller) is needed.
- Squish into 1440x810 even beats plain 1080p in calm1's center (38.09 vs 37.53), so it could be the first level.

## Caveats

- One game, one spin, synthetic calm pans, a hard cut between them. Real sessions change demand more gradually.
- Viewers **without the extension** would see a small picture in the top-left with black around it while starved
  (same dependency as the squish today).
- PSNR/SSIM reward blur; at the starved levels the visual difference (blocks vs soft) is larger than the numbers
  suggest — see the crops.
- The marker decoded on 100% of frames at both bitrates and cost nothing measurable (540 edge with / without:
  23.34 / 23.34 dB center, 6.44 / 6.45 Mbit/s).

## Conclusions

1. **Worth building.** Under starvation, shrinking the picture inside the frame gives the full low-res-stream gain
   (+1.9 dB center at 6 Mbit/s, +2.8 dB at 3 Mbit/s) with no restart, and switching mid-GOP is free.
2. **Pad with black, picture top-left.** Not edge-repeat, not mirror.
3. **It must be dynamic**: a fixed low level loses 6–10 dB in calm scenes. Levels 1080 / 720 / 540 / 360 (+ squish
   variants) covered every case here.
4. **Detection is cheap and works**: a 240x135 temporal-difference metric tracks encoder demand (r = 0.997); add
   uplink congestion as a second input. Drop fast, rise slowly.
5. **Signal per frame in the picture** (160x16 marker): reliable, free, and frame-accurate, which a stream-title
   tag can't be.
