# Next round: work plan for agents

Scope: everything under "What the next round of work proves" in the investor pitch, re-aimed at the real target.
The target is a solution stack that any mid-size live video platform can integrate (drone racing is the example
vertical). OBS, the browser extension and Twitch are the demo, not the product.

## What the last round established (`research/stack/README.md`)

- Against a full-resolution stream, squish wins clearly when the stream is starved. That part holds.
- Against the fair control, a plain downscale to the same pixel count, squish does not win on whole-frame quality in
  most tests. What it reliably adds is a sharper centre: 1.0 to 3.2 dB at 1080p with x264, in every segment measured.
- So the claim to prove next is **"the centre at native detail, at the bitrate of a lower-resolution stream"**, and the
  open question is whether viewers value that more than an evenly sharper frame. E5 and S8 answer it.
- It can be switched per 2 s segment by one rule, and it lowers encoder CPU. Encoder ROI costs the same CPU as a
  plain encode, sharpens the centre more, and lowers whole-frame quality on mixed footage.

The stack has five parts. Each task below builds or proves one of them.

| Part | What it is | State after this round |
|---|---|---|
| Squish module | Resamples a frame before the encoder; runs in a broadcaster app or in a platform's transcoder | Python reference and OBS shader exist; no native CPU module |
| Dynamic controller | Decides per 2 s segment whether to squish, from a cheap motion metric | Simulated on one clip (`research/stack`) |
| Signalling | Tells the player which mapping each segment uses | In-band marker and title code only; out-of-band not built |
| Player SDK | Undoes the squish in the viewer's player | Browser extension only |
| Restorer (optional) | 10k-parameter edge restorer in the player | Offline only (epic #20) |

## Rules for every task

1. Work in `Dayset/DBpO-OBS-filter`, commit to `main`, name the task (for example `E1`) in each commit message.
2. Every number in a README must come from a JSON file committed next to it. Label claims C (raw file), N (one clip, or
   code-read only) or U (no data), as issue #33 does.
3. Use the metric definitions in `research/stack/stack_bench.py` unchanged: luma PSNR, the 30% x 30% centre box,
   VMAF v0.6.1 at the reference size, first second skipped, 2 s segments (120 frames at 60 fps).
4. Encodes: CBR, bufsize 2 x rate, IDR every 120 frames, unless the task says otherwise. Record the bitrate each arm
   actually produced.
5. Every comparison carries the control arm: a plain downscale to the same pixel count as the squish arm, upscaled
   bicubic by the viewer (`scale810` for `squish56`). A squish result without its control is not a result.
6. Runners must resume: a finished arm is skipped on restart. Never overwrite a result file; add keys.
7. Footage comes from this repo's GitHub releases only. New footage needs a line in `research/stack/FOOTAGE.md` with
   source, licence and sha256. Do not download streams or videos you have no licence for.
8. A negative result is a result. If squish loses, write that down with the numbers. Do not tune an arm until it wins.
9. If a task's premise fails (see "Stop and report" in the task), stop that task, write the finding with its
   numbers in the task's README section, and tell the owner. Do not start dependent tasks.
10. New stack code must not depend on OBS, Twitch or a browser extension.
11. "Runs on" says where the task can run: **cloud** (any Linux sandbox with ffmpeg, 2+ cores), **PC** (the owner's
    Windows 11 machine with the RTX 5080; deliver a resumable `.bat` or `.ps1` runner and let the owner start it), or
    **people** (needs humans with devices; the agent builds the tool and analyses what comes back).

## Order

```
E1 ROI on GPU ──────────────┐
E2 More content ────────────┼─> E3 Full grid ─> S2 Controller ─┐
E4 Other codecs ────────────┘        ^                         │
E5 Centre-weighted metric ───────────┘                         │
S1 Native squish module ───────────────────────────────────────┼─> S4 Transcoder ─> S6 End-to-end rehearsal
S3 Signalling spec ─> S5 Player SDK ───────────────────────────┘        │
                              └─> S7 Device timing (people)             └─> S8 Blind viewer test (people)
S9 Demo hardening (independent)
```

E1, E2, E4, E5, S1, S3 and S9 can start at once.

---

## E1. Encoder ROI baseline on GPU encoders

- **Proves:** squish is better than the obvious alternative on the encoders platforms use, not only on x264.
- **Known:** x264 veryfast, 6000 kbit/s (`roi_v3.json`, `full_v4.json`). On the 102 s clip ROI lifts centre PSNR to
  28.3 dB (squish 25.6, plain 23.4) but lowers whole-frame VMAF to 40.6 (plain 45.3, squish 48.3). ROI costs the same
  encoder CPU as plain; squish cuts it by 37%.
- **Runs on:** PC.
- **Steps:**
  1. For each of `h264_nvenc`, `hevc_nvenc`, `av1_nvenc`, `libx265`, `libsvtav1`: encode 2 s of the v3 clip with and
     without `addroi` (centre box, qoffset -0.3). If centre PSNR changes by less than 0.3 dB the encoder ignores ROI
     through ffmpeg. Record that as a finding; it is an argument for squish, which needs no encoder support.
  2. For encoders that honour ROI: arms `plain`, `scale810`, `squish56`, `squish80`, `roi2zone`, `squish56+roi10` at
     3000, 6000 and 12000 kbit/s on v3 and the full v4 clip.
  3. Record encode time per frame for every arm.
- **Deliverables:** `research/stack/roi_gpu.json`, a section in `research/stack/README.md`, the runner script.
- **Done when:** one table per encoder with VMAF, centre PSNR, whole PSNR and encode time, plus the list of encoders
  that cannot do ROI.
- **Stop and report if:** ROI beats squish on both whole-frame VMAF and centre PSNR on the full v4 clip for any encoder.

## E2. Content variety

- **Proves:** the result is not one game and one clip.
- **Runs on:** PC for capture (owner), cloud or PC for the runs.
- **Needs from the owner:** at least three non-PlanetSide games and at least two FPV drone-racing clips, each 60 s or
  longer, 1080p60 or better, lossless or 50 Mbit/s and up, uploaded as release assets. Without this the task is blocked.
- **Steps:** add each clip to `FOOTAGE.md`; run `plain`, `scale810`, `squish56`, `squish80`, `roi2zone` at 3000, 6000
  and 12000; keep per-frame arrays; compute the detector metric per segment.
- **Deliverables:** `research/stack/content.json`, per-clip tables, one pooled table of per-segment VMAF gain.
- **Done when:** for each clip the README states, against plain and against the control: the share of segments where
  squish wins on whole-frame VMAF and on centre PSNR, the median and 5th-percentile difference, and the correlation
  with the detector metric.
- **Stop and report if:** squish's centre advantage over the control falls below 0.5 dB on any clip. For FPV, also
  check where the important content sits; if it is not the centre, say so.

## E3. Full bitrate, size and frame-rate grid

- **Proves:** where the method helps and where it stops, as one surface instead of single points.
- **Runs on:** PC (about one night).
- **Steps:** all clips from E2 plus v3 and v4; bitrates 2000, 3000, 4500, 6000, 9000, 12000, 16000; arms `plain`,
  `scale810`, `scale720`, `squish56`, `squish80`; frame rates 30 and 60, and 120 where the source allows (add `--fps` to the runner).
- **Deliverables:** `research/stack/grid.json`, a crossover table (bitrate at which squish stops winning, by PSNR and by
  VMAF, per clip and frame rate), charts.
- **Done when:** the README replaces every "about 2x VMAF" statement with the measured distribution, and issue #33 gaps
  G1, G2 and G3 are closed or restated.

## E4. Other codecs

- **Proves:** the gain does not disappear with HEVC or AV1.
- **Known:** first x265 and SVT-AV1 numbers on the v3 clip are in `research/stack/codecs.json`. With x265 the centre
  advantage over the control holds (1.2 to 1.5 dB) and whole-frame VMAF is within 2 either way. The SVT-AV1 arms missed
  their target bitrates by up to 19%, so they do not count yet.
- **Runs on:** cloud for software encoders, PC for NVENC HEVC and AV1.
- **Steps:** full v4 clip, `plain`, `scale810` and `squish56` at 3000, 6000 and 12000 for `libx265`, `libsvtav1`, `hevc_nvenc`,
  `av1_nvenc`. SVT-AV1 refuses CBR outside low-delay mode; use its low-delay mode and note it.
- **Done when:** a table of VMAF gain per codec and bitrate next to the x264 row.

## E5. Centre-weighted quality metric

- **Proves:** whether a sharper centre is worth more than an evenly sharper frame, in numbers a reviewer accepts.
- **Why:** every whole-frame metric treats the corner of the picture like the crosshair. The method's only reliable
  advantage is the centre, so the evidence needs a metric that weights where viewers look.
- **Runs on:** cloud.
- **Steps:**
  1. Desk research: published gaze distributions for first-person games, FPV and general video. Cite every source;
     record the spread of gaze around the screen centre.
  2. Add two metrics to `stack_bench.py`: VMAF of the centre crop, and PSNR weighted by a 2D Gaussian fitted to the
     published gaze spread. Keep the existing metrics unchanged.
  3. Recompute every arm in `research/stack` with both.
- **Deliverables:** `research/stack/GAZE.md`, updated JSON and tables.
- **Done when:** the README states, per clip and bitrate, which of plain, control, ROI and squish wins under the
  gaze-weighted metric, and how wide the gaze spread must be before the control wins.
- **Stop and report if:** the literature puts less than half of gaze time inside the 30% centre box for the target
  content. Then the centre box, or the whole premise, needs to change.

---

## S1. Native squish module

- **Proves:** a transcoder can run the squish at a cost below what the encoder saves.
- **Known (one CPU thread, 1080p to 1440x810):** Python area-filter reference 40 ms per frame; point-sampled OpenCV remap
  6.4 to 6.9 ms; plain OpenCV area resize 8.7 ms; x264 veryfast saves about 16 ms per frame by encoding 56% of the
  pixels. The point-sampled squish scored the same as the area-filtered one on the v3 spin (VMAF 50.6 against 50.9).
- **Runs on:** cloud.
- **Steps:**
  1. Build `stack/libsquish` in C from `plugins/foveated-squish/src/squish-math.c`: precomputed per-axis tap tables,
     separable, 8-bit planar YUV 4:2:0 in and out. Two quality levels: area-filtered and point-sampled.
  2. Add a command-line filter over yuv4mpeg pipes (`ffmpeg ... -f yuv4mpegpipe - | dbpo-squish ... | ffmpeg -i - ...`)
     and the inverse (`dbpo-unsquish`) for tests.
  3. Test against the Python golden vectors; time single-thread at 1080p and 1440p.
- **Deliverables:** `stack/libsquish/`, tests in CI, `research/stack/module_timing.json`.
- **Done when:** mean difference to the Python reference is at most 1/255, and the area-filtered path takes at most
  5 ms per 1080p frame on one thread of the PC.
- **Stop and report if:** the area path cannot get under 16 ms; then squish at transcode costs CPU instead of saving it.

## S2. Segment-level dynamic controller

- **Proves:** the method can be switched on only when a segment needs it, with no manual setting.
- **Known:** the simulation on the full v4 clip, in `research/stack/README.md` ("Dynamic use"). One rule, squish when the
  estimated demand exceeds the budget, reached 99% and 94% of the oracle's gain at 6000 and 12000 kbit/s (97% and 88%
  when decided one segment late). The clip is demanding almost throughout, so the rule has not been tested on calm
  content.
- **Runs on:** cloud. Depends on E3 for thresholds across content.
- **Steps:** a function `decide(metric_history, bitrate, pixels) -> level` with levels plain, plain downscale,
  squish80, squish56; the target it optimises (whole-frame VMAF or the E5 metric) is a parameter;
  thresholds expressed as demand over budget so they move with bitrate; hysteresis of one segment; a decision log.
  Evaluate both variants: decision from the previous segment (live broadcaster) and from the current segment (a
  transcoder that already buffers one segment).
- **Deliverables:** `stack/controller/`, `research/stack/controller.json`.
- **Done when:** on every E3 clip and bitrate the controller reaches at least 90% of the oracle's VMAF gain over plain and
  is never more than 1 VMAF below the better static choice.

## S3. Signalling, out of band

- **Proves:** the player learns the mapping without anything that a platform's transcode can destroy.
- **Background:** the in-band marker and the title code were built for the demo. A platform re-encode can break the
  marker. The mode is a property of a segment, so it belongs with the segment's metadata.
- **Runs on:** cloud.
- **Steps:** write `stack/SIGNALLING.md` with one payload (the existing compact FSQ1 code plus a mode id and a segment
  sequence number) and four carriers, in order of preference:
  1. HLS playlist: a tag per segment (custom tag or `EXT-X-DATERANGE` attribute).
  2. DASH: an `EventStream` or `emsg` box.
  3. A side channel (WebSocket or server-sent events) keyed by segment sequence number or program date-time, for
     platforms whose packager cannot be changed.
  4. The in-band marker, kept only as a last fallback.
  Add test vectors for the payload and a rule for what the player does when the signal is missing (show the picture
  as plain and report it).
- **Done when:** the spec and vectors are merged and S5 passes its test with carriers 1 and 3.

## S4. Reference transcoder

- **Proves:** a platform can apply the method on its own servers, with no change for the broadcaster.
- **Known:** `research/stack/server.json`: a 6000 kbit/s ingest transcoded to 3000, 1500 and 800 kbit/s rungs. At the
  same pixel count squish was 1.5 and 1.1 VMAF below the plain downscale at the 720p and 480p rungs and 0.8 above at
  360p; it won centre PSNR at every rung (by 0.6 to 0.8 dB) and needed 10% less encoder CPU.
- **Runs on:** cloud. Depends on S1, S2, S3.
- **Steps:** an ffmpeg-based script: ingest (file, RTMP or SRT) to an HLS ladder with 2 s segments. For each rung and
  segment the controller picks plain or squish. Test two ways to switch and keep the one that plays cleanly:
  (a) coded size changes at the segment boundary, with a discontinuity; (b) coded size fixed, squished picture in the
  top-left with black padding, which earlier work measured as free. Write the S3 tag for each segment.
- **Deliverables:** `stack/transcoder/`, a recorded sample stream as a release asset.
- **Done when:** the output plays in an unmodified HLS player (as a squeezed picture) and in the S5 player (correct
  picture), and transcode CPU per rung is at or below the plain ladder's.

## S5. Player SDK (web)

- **Proves:** any platform can add the viewer side to its own player; no extension.
- **Runs on:** cloud.
- **Known:** a bicubic unsquish scored 2.3 to 6 VMAF above the bilinear one the extension uses. Use bicubic.
- **Steps:** a small library on top of hls.js: WebGL2 unsquish reusing `viewer/unsquish-extension/squish.js`, mapping
  chosen per displayed frame from the segment it belongs to (`requestVideoFrameCallback`), carriers 1 and 3 from S3.
  Check what Safari on iPhone needs (native HLS, metadata cues) and record what works.
- **Deliverables:** `stack/player/`, a demo page, an end-to-end test in headless Chromium.
- **Done when:** a 10-minute stream with at least 100 mode switches, seeks and rung switches shows zero frames drawn
  with the wrong mapping (checked with a per-segment test pattern).

## S6. End-to-end rehearsal

- **Proves:** the whole chain works outside the lab, including a platform that re-encodes. Replaces "live Twitch session".
- **Runs on:** cloud. Depends on S4 and S5.
- **Steps:**
  1. Broadcaster (ffmpeg or OBS) to ingest to S4 to static HTTP to the S5 player. Record glass-to-glass behaviour at
     switches.
  2. Hostile platform: pass S4's output through a second transcode (re-encode and rescale to 720p), then play with the
     side channel carrier. Measure VMAF against the same re-transcode of a plain stream.
- **Done when:** a report states whether the picture and the signalling survive, with numbers for case 2.

## S7. Device timing

- **Proves:** every viewer device can run the player side.
- **Known:** the viewer's unsquish takes 11.8 ms per 1080p frame on one CPU thread, so it must run on the GPU.
- **Runs on:** people. Depends on S5.
- **Steps:** add a benchmark mode to the S5 demo page: frame time of the unsquish pass, dropped frames, decoded size,
  device string; upload as JSON. The owner opens the page on each device.
- **Done when:** at least five devices are in the table (one laptop with integrated graphics, one mid-range Android
  phone, one iPhone), and each holds 60 fps at 1080p or the README states the size it can hold.

## S8. Blind viewer test

- **Proves:** people prefer the result, not only the metrics. This is the decisive test for the centre claim.
- **Runs on:** people. Depends on S6.
- **Steps:** build on the blinded renderer from commit ee5534a. Write `research/viewer-test/PROTOCOL.md` before
  collecting anything: two-alternative forced choice, random side, pairs drawn from plain, plain downscale (the control),
  dynamic squish and ROI at the same bitrate; squish against the control is the pair that matters; at least 8 clips and 2 bitrates, at least 20 viewers. An analysis script reports preference with a
  confidence interval per pair.
- **Done when:** the protocol was committed before the first response, and the result is in the README whichever way
  it goes.

## S9. Demo hardening

- **Proves:** nothing new; it stops the demo from breaking in front of someone.
- **Runs on:** cloud for code, PC for the OBS runtime check.
- **Steps:** fix issue #33 errors S1 and S2 (the view selector must not change what is encoded), S4 (title code length
  check), and replace the hand-typed saving table (S6) with the measured ranges from `research/stack`.
- **Done when:** the OBS runtime test on the PC shows the encoder output unchanged when the view is switched.
