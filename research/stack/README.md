# Platform-stack measurements: ROI, the downscale control, dynamic use, server-side transcode (issue #33)

Run on 2026-11-15 in a 2-core Linux sandbox. x264 veryfast CBR unless a row says otherwise, 1080p60, all four
PlanetSide 2 release clips. Raw results: the `*.json` files here. Runner: `run_all.sh` (resumes). Tables: `report.py`.

## What changed

1. **Against a full-resolution stream the squish wins when the stream is starved.** Confirmed on three more clips and
   with VMAF at 1080p on every frame.
2. **Against the fair control, a plain downscale to the same pixel count, it does not win on whole-frame quality.**
   With the same viewer scaler on both sides it wins on the two fast spins, ties on mixed footage at 6000 kbit/s,
   and loses on mixed footage at 12000 and on the slow spin. Most of the gain over a 1080p stream comes from encoding fewer pixels, which a platform can get by
   lowering the resolution.
3. **What the squish adds over the control is the centre: 1.0 to 3.2 dB sharper, in every clip and every segment.**
4. **Encoder ROI is not weak.** It sharpens the centre more than the squish does. It costs the same encoder CPU as a
   plain encode, and on mixed footage it lowers whole-frame VMAF below the plain stream.
5. **The squish lowers encoder CPU by 37%** (22% net with a point-sampled resample, which scored the same).
6. **One rule switches it per 2 s segment:** squish when the estimated demand exceeds the budget. It reached 94% to
   99% of the best per-segment choice at 6000 and 12000 kbit/s.
7. **Server side, on a lossy ingest, the plain ladder was as good or better on whole-frame VMAF.** The squish kept a
   0.6 to 0.8 dB centre advantage at every rung.

The claim the data supports is "the centre at native detail, at the bitrate and encoder cost of a lower-resolution
stream". It does not support "X% less bandwidth for the same picture". Whether viewers prefer a sharper centre to an
evenly sharper frame is not measured.

## Method

- Reference: each clip decoded to 1080p60 (`fps=60,scale=1920:1080:flags=area`) and stored lossless.
- Arm: one encode (bufsize 2 x rate, IDR every 120 frames), decoded and brought back to 1080p the way a viewer would.
- Metrics per frame against the reference: luma PSNR over the frame and over the 30% x 30% centre box (the area the
  squish keeps 1:1), and VMAF v0.6.1 at 1080p. Summaries skip the first second. "p5" is the 5th percentile of frames.
- Encoder CPU: `ffmpeg -benchmark` user + system time of the encode minus a decode-only pass of the same input.
- Arms: `plain` 1080p. `squish56` 1440x810, `squish80` 1712x964 (centre 30% kept 1:1, linear curve, bilinear
  unsquish as the extension does; `...c` / `cubic-unsquish` = bicubic unsquish). `scale810`, `scale720` = plain
  bicubic downscale, bicubic upscale by the viewer. `roi...` = x264 with `addroi` on the centre box (QP offset -5, -10,
  -15; `roi2zone` = -15 on the centre box and -7.5 on a 60% box around it).
- Clips: v1 "average spin" 15 s, v2 "slowest spin" 15 s, v3 "left spin" frames 240-599, v4 "different speed" 102 s of
  mixed play (walking, driving, turning). v5 is a 6 Mbit/s recording whose motion profile matches v4 (it looks like the same session) and is not used.

## 1. Encoder ROI against squish

v3 spin, 6000 kbit/s (300 frames):

| Arm | VMAF | PSNR dB | Centre PSNR dB | Encoder CPU ms/frame |
|---|---|---|---|---|
| plain 1080p | 31.4 | 23.47 | 21.63 | 42.5 |
| ROI -5 QP | 33.9 | 23.58 | 23.78 | 41.3 |
| ROI -10 QP | 36.1 | 23.46 | 26.29 | 41.7 |
| ROI -15 QP | 36.7 | 23.09 | 28.36 | 41.5 |
| ROI 2 zones | 39.7 | 23.37 | 26.79 | 41.4 |
| squish80 | 39.9 | 24.75 | 23.31 | 35.5 |
| squish56 | 50.9 | 25.65 | 25.49 | 26.7 |
| squish56 + ROI -10 QP | 45.8 | 24.50 | 29.70 | 26.4 |

v4, 102 s of mixed play, 6000 kbit/s (6087 frames):

| Arm | VMAF | VMAF p5 | PSNR dB | Centre PSNR dB | Encoder CPU ms/frame |
|---|---|---|---|---|---|
| plain 1080p | 45.3 | 25.4 | 24.68 | 23.41 | 36.1 |
| ROI 2 zones | 40.6 | 26.4 | 23.24 | 28.26 | 33.6 |
| squish56 | 48.3 | 30.2 | 24.68 | 25.58 | 22.8 |
| squish56, bicubic unsquish | 50.9 | 31.5 | 24.79 | 25.57 | 22.4 |

- ROI raises centre PSNR by up to 6.7 dB on the spin and 4.8 dB on the mixed clip, more than the squish. The repo's
  earlier line that ROI gives "not even +5%" is wrong: on the spin it gives +8% to +26% VMAF.
- ROI pays for the centre with the rest of the frame. On the mixed clip whole-frame PSNR falls 1.4 dB and VMAF falls
  4.7 below plain. The squish raises centre and whole frame together.
- ROI does not cost more encoder CPU than plain (41 against 42.5 ms). The difference is that the squish costs less.
- The two stack: squish56 plus a mild ROI gave the sharpest centre of any arm (29.7 dB) at squish's encoder cost.
- At 3000 kbit/s on the spin ROI did almost nothing (centre +0.7 to +0.8 dB); squish56 gave +2.0 dB centre, +1.7 dB
  whole frame.

## 2. The control: plain downscale to the same pixel count

| Clip, encoder, kbit/s | VMAF plain 1080p | VMAF downscale 810p | VMAF squish56 bilinear / bicubic unsquish | Centre PSNR dB, downscale to squish | Whole PSNR dB, downscale to squish |
|---|---|---|---|---|---|
| v3 fast spin, x264, 6000 | 31.4 | 46.3 | 50.9 / 53.2 | 23.50 to 25.49 (+1.99) | 25.55 to 25.74 |
| v3 fast spin, x264, 3000 | 22.1 | 22.1 | 23.7 / not run | 20.29 to 21.32 (+1.03) | 21.92 to 21.85 |
| v1 average spin, x264, 6000 | 41.2 | 57.8 | 59.4 / 62.5 | 25.19 to 27.57 (+2.38) | 27.17 to 27.28 |
| v2 slowest spin, x264, 6000 | 78.5 | 80.3 | 67.4 / 73.3 | 29.11 to 32.31 (+3.20) | 29.96 to 28.41 |
| v4 mixed 102 s, x264, 6000 | 45.3 | 51.0 | 48.3 / 50.9 | 24.01 to 25.57 (+1.56) | 25.28 to 24.79 |
| v4 mixed 102 s, x264, 12000 | 65.1 | 69.1 | 63.4 / 67.1 | 26.07 to 28.18 (+2.10) | 27.38 to 26.52 |
| v3 fast spin, x265, 6000 | 47.8 | 50.4 | 49.9 / not run | 24.00 to 25.45 (+1.45) | 26.04 to 25.53 |
| v3 fast spin, x265, 3000 | 29.7 | 32.1 | 33.7 / not run | 22.08 to 23.25 (+1.17) | 23.98 to 23.67 |

(Whole PSNR for squish is the bicubic-unsquish arm where one was run.)

- A plain downscale gets most of what the squish gets over a 1080p stream: on the v3 spin 46.3 against 50.9 VMAF,
  from 31.4. Lowering the resolution is the bigger lever; this matches the earlier starvation result.
- Over that control, the squish's whole-frame result depends on content: +4.7 and +6.9 VMAF on the two fast spins,
  -0.1 on the mixed clip at 6000, -2.0 at 12000, -6.9 on the slow spin (bicubic on both sides).
- The centre advantage is the same sign everywhere: +1.0 to +3.2 dB, and it grows as the content gets calmer.
- The viewer's scaler matters. Bicubic unsquish scored 2.3 to 6 VMAF above the bilinear one the extension uses.
- On the mixed clip, per 2 s segment (bicubic): squish beat the control on VMAF in 25 of 51 segments at 6000 and 7 of
  51 at 12000, and on centre PSNR in 51 of 51 at both.

## 3. Dynamic use (v4, 51 segments of 2 s)

Both streams are encoded whole with a closed GOP every 2 s, and the viewer gets, per segment, the stream the rule
picks. That is how adaptive streaming already switches. Demand is estimated from the 240x135 frame-difference metric
(1.35 Mbit/s per unit, from `research/foveation/starvation_findings.md`).

| Policy | 6000: segments squished | 6000: VMAF | 12000: segments squished | 12000: VMAF |
|---|---|---|---|---|
| always plain 1080p | 0 | 45.64 | 0 | 65.37 |
| always squish56 | 51 | 51.16 | 51 | 67.34 |
| squish when demand exceeds the budget, this segment | 50 | 51.26 | 44 | 67.69 |
| same rule, decided from the previous segment | 49 | 51.17 | 43 | 67.53 |
| squish when demand is at least 2 x budget, this segment | 44 | 51.04 | 13 | 66.60 |
| best of plain and squish per segment (oracle) | 48 | 51.33 | 38 | 67.84 |
| always plain downscale to 810p (control) | - | 51.36 | - | 69.32 |

(Squish arm with bicubic unsquish. Means are over segments. Every row landed within 1.3% of its target bitrate.)

- Squish beat the 1080p stream in 48 of 51 segments at 6000 and 38 of 51 at 12000. It lost in the calmest ones, by up
  to 4.9 and 6.5 VMAF.
- The rule "squish when the estimated demand exceeds the budget" reached 99% and 94% of the oracle's gain. Decided
  one segment late, as a live broadcaster must, it reached 97% and 88%. It needs no per-stream setting.
- On this clip the rule adds only 0.1 and 0.35 VMAF over always-on squish, because the clip is demanding almost
  throughout. Its job is to leave calm content alone; a calmer clip is needed to show what that is worth.
- Squish's gain over plain tracks the motion metric (correlation 0.62 at 6000, 0.76 at 12000), which is why a cheap
  detector is enough. The rule and its threshold were chosen on this clip.
- The control wins here too: an 810p stream beat the 1080p stream at both bitrates, and beat the dynamic squish by
  0.1 VMAF at 6000 and 1.6 at 12000. The squish's centre was 1.6 and 2.1 dB sharper than the control's in all 51
  segments.

## 4. Server side: squish applied at transcode

A 20 s fast section of v4 (seconds 72-92) is encoded as a 6000 kbit/s 1080p ingest (VMAF 40.9 against the pristine
source), then transcoded from that ingest to lower rungs. Scores are against the pristine source.

| Rung | Arm | Pixels | VMAF | PSNR dB | Centre PSNR dB | Encoder CPU ms/frame |
|---|---|---|---|---|---|---|
| 3000 kbit/s | plain 1080p | 1920x1080 | 22.0 | 22.24 | 21.15 | 29.1 |
| | ROI 2 zones, 1080p | 1920x1080 | 23.9 | 21.89 | 22.90 | 28.1 |
| | plain 720p | 1280x720 | 25.5 | 23.07 | 22.05 | 17.6 |
| | squish of 1080p into 1280x720 | 1280x720 | 23.9 | 22.10 | 22.64 | 15.8 |
| | squish of 720p into 960x540 | 960x540 | 22.8 | 22.81 | 22.67 | 11.0 |
| 1500 kbit/s | plain 480p | 854x480 | 18.8 | 22.37 | 21.42 | 8.3 |
| | squish of 720p into 854x480 | 854x480 | 17.7 | 21.47 | 22.13 | 7.4 |
| | squish of 480p into 640x360 | 640x360 | 15.0 | 22.10 | 22.05 | 4.6 |
| 800 kbit/s | plain 360p | 640x360 | 12.7 | 21.70 | 20.78 | 4.8 |
| | squish of 540p into 640x360 | 640x360 | 13.4 | 20.84 | 21.55 | 4.3 |
| | squish of 360p into 480x270 | 480x270 | 10.2 | 21.53 | 21.51 | 2.5 |

- It works mechanically: the transcoder already resamples every rung, and the squish replaces that step.
- At the same pixel count the squish is 1.5 and 1.1 VMAF below the plain rung at 720p and 480p, and 0.8 above at
  360p. It is 0.6 to 0.8 dB sharper in the centre at every rung, at about 10% less encoder CPU.
- Squishing a rung further (56% of its pixels) lowers VMAF by 2.5 to 3.8 and saves 37% to 48% of that rung's encoder
  CPU. Centre PSNR stays 0.6 to 0.7 dB above the plain rung.
- These squish arms used a bilinear unsquish, so they are a little understated (see section 2).
- One 20 s section, one content type. Read it as "no whole-frame win found yet on the server side".

## 5. Compute

| Step, 1080p to 1440x810, one CPU thread | ms per frame |
|---|---|
| x264 veryfast encode, plain 1080p at 6000 | 42.5 |
| x264 veryfast encode, squished 1440x810 at 6000 | 26.7 |
| x264 veryfast encode with ROI | 41.3 to 41.7 |
| squish, area-filtered Python reference | 38.5 to 40 |
| squish, point-sampled (one bilinear lookup per output pixel, OpenCV) | 6.4 to 6.9 |
| plain area downscale (OpenCV) | 8.7 |
| viewer: decode plain / squished | 4.5 / 3.5 |
| viewer: unsquish on one CPU thread (OpenCV) | 11.8 |

- Encoding 56% of the pixels saves 15.8 ms per frame (37%). With the point-sampled squish the whole server step
  costs 33.0 ms against 42.5 ms for plain, 22% less. The point-sampled squish scored the same as the area-filtered
  one on the v3 spin (VMAF 50.6 against 50.9, centre 25.42 against 25.49 dB).
- The area-filtered reference in Python costs more than the encoder saves. A native module is not written yet.
- The viewer's unsquish at 11.8 ms on one CPU thread is too slow for 60 fps; it belongs on the GPU, where the
  extension already runs it. GPU and phone timing are still unmeasured.

## 6. Other codecs (v3 spin; first look)

| Encoder, kbit/s | VMAF plain / downscale 810p / squish56 | Centre PSNR dB plain / downscale / squish56 |
|---|---|---|
| x265 veryfast, 6000 | 47.8 / 50.4 / 49.9 | 23.67 / 24.00 / 25.45 |
| x265 veryfast, 3000 | 29.7 / 32.1 / 33.7 | 21.78 / 22.08 / 23.25 |
| SVT-AV1 preset 10, target 6000 | 50.1 / 53.6 / 50.5 | 23.67 / 24.02 / 25.18 |
| SVT-AV1 preset 10, target 3000 | 36.3 / 34.4 / 34.0 | 22.37 / 22.15 / 23.11 |

- With HEVC the gain over a 1080p stream shrinks to +2 to +4 VMAF, and the centre advantage over the control holds
  (+1.2 to +1.5 dB).
- The SVT-AV1 arms ran in target-bitrate mode and landed between 2514 and 5165 kbit/s, unequal across arms. They do
  not support a conclusion.

## Limits

One game. One 102 s mixed clip and three short spins. x264 for everything that carries a conclusion. No GPU encoder,
no device timing, no viewer test. The dynamic rule and the 2 x threshold were chosen on the clip they are reported
on. The dynamic result is a splice simulation of two separately rate-controlled streams, not one live encode.
The centre box is a fixed 30% x 30%; nothing here measures where viewers look.

## Reproduce

```
export VMAF_FFMPEG=/path/to/ffmpeg-with-libvmaf      # pip install imageio-ffmpeg ships one
bash research/stack/run_all.sh WORK FOOTAGE          # FOOTAGE holds v1.mp4 .. v4.mp4 from the releases
python3 research/stack/report.py WORK        # or "research/stack": the per-frame arrays of the v4 arms are committed in v4/
```
About 4 hours on 2 cores and 20 GB of scratch space.
