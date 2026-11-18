# Round 4: instructions for agents

Read this whole file before starting. It replaces the task lists in `ROUND3.md` and the next-round plan; their rules still apply where this file is silent.

## Where the project stands

The method (squish: centre kept at 1:1, sides squeezed, fewer pixels sent, player undoes it) has one supported result: on starved first-person footage, at the same bitrate, the centre is clearly better than a plain downscale (+2.0 dB centre-30% PSNR on HEVC and AV1, +12 to +15 centre VMAF on x264, one clip; centre PSNR higher in 56 of 56 cells on 7 clips), while whole-frame scores are about equal to the downscale (+1.7 VMAF x264, -1.5 VMAF-NEG HEVC and AV1).

Only one setting has ever been measured (30% centre, 1440x810), on one game, with no blind viewers.

## The finish

Three deliverables. Work that does not feed one of them is out of scope.

1. **Demo pack**: for at least 3 clips (2 FPV), same-bitrate files for plain, downscale and the method.
2. **Evidence sheet**: one page with the region profile per clip, bitrate saved at equal centre quality, and the blind-test result.
3. **Blind test result**: at least 10 viewers, protocol committed before the first answer.

## Rules

1. Every comparison has three arms at the same measured bitrate: `plain`, `downscale` (same pixel count as the method arm, bicubic upscale) and the method. A result without the downscale arm is not a result.
2. Bitrate is matched on the transmitted stream and measured over the scored frames. Report the measured value, not the target.
3. Clips are split once, in `research/next-round/CLIPS.md`, into **tuning** and **held-out**. Settings are chosen on tuning clips only. Held-out clips are run once, with the chosen settings. A CNN never sees a held-out clip.
4. Report every clip and every cell. A loss is a result. Do not tune an arm until it wins, and do not drop a clip after scoring.
5. Write the pass rule into the task's README section before running it.
6. Every number in a README comes from a JSON file committed next to it. Codec, arm and bitrate go in every file name.
7. Frames are paired by index at 60 fps. Clips under 10 s are refused. The first second is skipped.
8. Any arm with a CNN or sharpening also reports VMAF-NEG.
9. PC work is delivered as one resumable `.bat` per task that the owner starts. The owner's PC: Windows 11, RTX 5080, `C:\dbpo-lab`, Python 3.12 for torch (`py -3.12`, torch cu130 build), ffmpeg with libvmaf.
10. No repo push of method code marked private in the audit list without the owner's approval.
11. If a stop rule triggers, stop that task, write the numbers down, tell the owner, and do not start tasks that depend on it.

## Tasks

Order (revised 2026-11-17, see `claude/project-revision.md`): finish the held-out run; then a smooth-falloff ROI arm in T12; then the V3 platform re-encode test; then a 3 to 5 viewer blind-test pilot (T7); T9 prior art and patents runs now, in parallel, and now includes FPV digital links, teleoperation video, foveated ABR rungs and camera dewarp hardware; then T6 FPV clips with GPU timing; then T5 on NVENC through OBS with recorded settings; then T3 with four arms (plain, downscale, ROI, squish); then T11; T4 and T10 last; T8 at the end.

### T0. Put today's tools in the repo
- **Why:** the scripts that produced the current evidence exist only on the owner's PC.
- **Do:** commit `r1_runner.py`, `arms_decode.py`, `centre_vmaf.py`, `blind_pairs.py`, `compare_demo.py`, `r9_check.py` and the `stack_bench.py` changes (`QP_MODE`, `KEYINT_S`) under `research/next-round/` and `research/stack/`. Fix `arms_decode.py` so output names carry the real codec. Merge `centre_vmaf.py` and the centre PSNR command into one scorer that writes JSON.
- **Done when:** a clean checkout reproduces the 6000 kbit/s table in section "Where the project stands" from `ref60.mkv`.

### T1. Region profile: the metric that fits the method
- **Why:** whole-frame scores average the corner with the crosshair and net the method's trade to zero. The owner's view, backed by the data, is that a per-region measure shows what a whole-frame score hides.
- **Do:**
  1. Scorer outputs, per arm: VMAF and VMAF-NEG for centred boxes of 10, 20, 30, 50, 70 and 100% of the frame; luma PSNR and SSIM for the rings between those boxes (ring = box minus the next smaller box); the 5th percentile over frames for each.
  2. One chart per clip and bitrate: quality against distance from centre, one line per arm.
  3. Desk research (task E5 in the old plan): published gaze distributions for first-person games, FPV and esports viewing. Cite every source. Fit a 2D Gaussian and add one gaze-weighted score per arm. State how wide gaze must be before the downscale wins.
- **Pass rule (gate G1):** on at least 3 of 4 clips, the method beats the downscale in the 50% box by 1.5 dB or 5 VMAF at 3000 and 6000 kbit/s.
- **Stop and report if:** the literature puts less than half of gaze time inside the 30% box for first-person content.

### T2. Adaptive canvas: search the settings nobody has tried
- **Why:** the method can change its centre size, squeeze strength and transmitted size freely, which a fixed-resolution constant-bitrate pipeline cannot. Under starvation it could send a small canvas with a small crisp centre. Only 30% / 1440x810 has been measured; at 500 kbit/s that setting collapsed with every other arm.
- **Do:**
  1. Check that `Squish` in `stack_bench.py` accepts centre fraction and squeeze ratio as parameters; add them if not, with a round-trip test.
  2. Grid on tuning clips: centre fraction 5, 10, 20, 30, 50%; transmitted size 1440x810, 1280x720, 960x540, 640x360, 284x160; bitrates 500, 1000, 1500, 3000, 6000 kbit/s; x264 and hevc_nvenc. Each method cell gets a downscale arm at the same transmitted size.
  3. Score with the T1 profile. For each bitrate, report the settings on the Pareto front of centre-50% quality against outside-50% quality, and the best downscale rung.
  4. Confirm the chosen setting per bitrate on held-out clips, once.
- **Pass rule (gate G2):** at 1500 kbit/s or below there is a setting whose centre-50% VMAF beats the best downscale rung by at least 5 on held-out clips.
- **Stop and report if:** no setting beats the best downscale rung in the 50% box at any bitrate on tuning clips.

### T3. Turn it on only when needed
- **Why:** the gain is all in starved segments (method beats downscale in 91% of segments where plain VMAF is under 25, 0% where it is over 60).
- **Do:** per 2 s segment, a cheap motion or complexity measure predicts starvation; a rule picks plain, downscale or a T2 setting. Fit on tuning clips, test on held-out clips, against the per-segment oracle.
- **Pass rule:** at least 90% of the oracle's gain in gaze-weighted score; never more than 1 VMAF below the best static arm.

### T4. Viewer-side restoration
- **Why:** bicubic unsquish, the small CNN and sharpening of the sides have each shown gains, never together under clean conditions.
- **Do:** arms on the T2 setting: bilinear, bicubic, bicubic + CNN, bicubic + CNN with the centre left untouched, and the same with unsharp 0.25 and 0.5 applied outside the centre only. The downscale arm gets its own CNN. Leave-one-clip-out on tuning clips, then held-out.
- **Model size and training length:** the current net has 4 conv layers, 24 channels, about 12k parameters, a 9x9 pixel reach, 3000 steps, one setting, never varied. Grid on the T2 setting at 3000 kbit/s: channels 24, 48 or 96; depth 4 or 8; steps 3k, 10k or 30k. Report VMAF-NEG (centre 30% and whole frame) on held-out clips and GPU milliseconds per 1080p frame. The same net also runs as the upscaler of the downscale arm, so a bigger model cannot favour the method alone.
- **Pass rule:** a restoration arm improves outside-50% VMAF-NEG by at least 2 without lowering centre-30% PSNR by more than 0.3 dB.
- **Stop rule for size:** if 24 to 96 channels adds less than 1 VMAF-NEG on held-out clips, keep the small model. A model that needs over 8 ms per 1080p frame on the RTX 5080 is reported as too heavy for a game server.

### T5. Encoder emphasis on GPU
- **Why:** ffmpeg's `addroi` is ignored by NVENC (byte-identical output); x264 honours it (+4.65 dB centre). Platforms use GPU encoders.
- **Desk finding (2026-11-17, web search, not yet run on the PC):** the NVENC API has taken a per-block QP delta map since SDK 8.1 (`NV_ENC_PIC_PARAMS::qpDeltaMap`, `rcParams.qpMapMode = NV_ENC_QP_MAP_DELTA`; the delta is added to the rate-control QP). OBS Studio 30.1 and later wires it to its own native NVENC encoders for H.264, HEVC and AV1 (map blocks 16, 32 and 64 pixels; encoder capability `OBS_ENCODER_CAP_ROI`), and the `obs-roi-ui` plugin (GPL-2.0, derrod) offers a centre-focus region, rectangular or radial, for NVENC, AMF, QSV and x264. So an NVENC ROI path exists for OBS users. What I did not find: ffmpeg's `h264_nvenc` reading `addroi` side data (consistent with our identical-output result, not confirmed in source); any NVENC ROI quality measurement.
- **Do:** (1) confirm the OBS route on the PC: OBS 30.1 or later, native NVENC (not the ffmpeg one), `obs-roi-ui` centre-focus region, record the same clip with and without it at 3000 and 6000 kbit/s CBR, H.264, HEVC and AV1; (2) if a scripted route is wanted, check whether PyNvVideoCodec or a small C program against `nvEncodeAPI.h` can pass `qpDeltaMap`; (3) score with the T1 profile; arms `roi` and `method + roi`, against the best downscale.
- **Pass rule:** centre-30% PSNR moves by more than 0.3 dB on NVENC H.264, with the cost outside the centre reported.
- **Consequence written before the run:** if the pass rule holds, NVENC is no longer a place where ROI cannot reach, and the method's claim narrows to starvation depths where the QP range caps ROI, to canvas flexibility, and to encoders or services without ROI input. T12's x264 result is then the best available model of ROI.
- **Stop and report if:** OBS-native NVENC ROI cannot be driven reproducibly and no scripted route exists; then NVENC ROI stays unmeasured and the pitch says so.

### T6. Content
- **Why:** all evidence is one game.
- **Do:** list every clip in `CLIPS.md` with source, length, sha256, tuning or held-out, and a motion label (mean absolute frame difference at quarter resolution). Needed from the owner: at least 2 FPV clips, 60 s or longer, 1080p60, lossless or 50 Mbit/s and up. For FPV, check where the important content sits in the frame.
- **Stop and report if:** in FPV footage the important content is not near the centre.

### T7. Blind test
- **Why:** this is the finish. People get A and B at the same bitrate and pick.
- **Do:** follow `blind-test-protocol.md` (copy it into the repo unchanged). Pairs: method against downscale (primary) and method against plain, built by `arms_decode.py` with the T2 setting, on every clip. Extend `blind_pairs.py` to take two pair sets.
- **Pass rule (gate G3):** method preferred over downscale on the centre question, sign test p < 0.05, at least 10 viewers. Sides question reported next to it.

### T8. Pitch pack
- **Do:** the demo pack, the one-page evidence sheet, and a cost line using the round-3 money table with the measured saving at equal centre quality. No claim that a gate did not pass.

### T9. Research: is there a better way, and is this new?
- **Why:** the owner wants the method improved by looking outward, not only by re-running tests.
- **Do:** a cited written review in `research/next-round/PRIOR_ART.md` covering: foveated and region-adaptive video coding; non-uniform resampling before encode (log-polar, saliency-driven warping, content-aware retargeting); AV1 reference scaling and super-resolution modes; per-title and per-scene resolution ladders; VR and cloud-gaming foveated streaming; learned restoration at the player. For each: what it does, how it differs, whether it suggests a change to our mapping (for example a smooth falloff instead of a hard 1:1 box, or a gaze-fitted curve). End with up to 5 concrete experiments ranked by expected gain per day of work.
- **Done when:** each proposed experiment names the T1 number it should move and by how much.

### T10. Selectable focus: nine variants, switch on the keyframe
- **Why:** the viewer picks the sharp block (numpad 1 to 9, centre 5), which stands in for a gaze tracker. All viewers share the nine variants, so a CDN can cache them; a per-viewer encode cannot be cached. T9-style research is not needed for the premise: a focus at the viewer's choice beats a fixed centre by construction.
- **Do:**
  1. `SquishP` in `grid_t2.py` takes a focus position (fx, fy); `Axis` already has the parameter. Nine variants: focus at the centres of a 3x3 grid.
  2. Quality check: for each focus, centre-of-focus VMAF and PSNR against the same box in the centre variant. Record any position where the density floor forces a milder setting at the frame edge.
  3. Switching: stitch segments from two variants at keyframe boundaries (keyframe interval 0.5, 1 and 2 s) with the per-segment mapping signalled as in S3; check there are no frames drawn with the wrong mapping, and measure the bitrate cost of the shorter interval.
  4. Cost model: encode compute and storage for 9 variants against 1, and for a reduced set (centre plus four corners).
  5. Blind test arm: switch-on versus fixed centre, on a clip where the target leaves the centre (FPV or racing).
- **Pass rule:** off-centre focus keeps centre-of-focus quality within 1 dB of the centre variant at 7 of 9 positions; keyframe switching shows no wrong-mapping frames over 100 switches.
- **Stop and report if:** a 0.5 s switch is visibly different from no switch to more than half of the viewers in the T7 test.

### T11. Cloud gaming relevance (NVIDIA and others)
- **Why:** cloud gaming is one-to-one, the server knows the focus (crosshair, aim target), and nobody public ships focus-aware squeeze for flat-screen streams. Today's evidence is at 3 to 6 Mbit/s on H.264, far below GeForce NOW-class rates (about 25 Mbps for 1080p60, about 47 Mbps for 4K), and the codec there is mostly AV1.
- **Do:** (1) AV1 NVENC at 5, 10, 15 and 25 Mbit/s on the tuning clips and `game2`, plain, downscale and the best T2 settings; (2) 120 fps clips if the owner can record them (otherwise say untested); (3) GPU time of squish and unsquish on the RTX 5080, in ms per 1080p frame; (4) a focus driven by a game signal (T10 mapping), reported as a mock-up with the crosshair position from a log if one exists.
- **Pass rule:** at 15 Mbit/s or below on AV1, some setting beats the best downscale by 5 VMAF in the centre-50% box, and squish plus unsquish together take under 2 ms per 1080p frame on GPU.
- **Stop and report if:** the gain is under 1 VMAF-NEG everywhere above 10 Mbit/s; then the pitch for cloud gaming is limited to weak links and says so.

### T12. Encoder ROI head to head
- **Why:** encoder ROI is the closest rival. It needs no player change and also moves bits to the centre. The method has to beat it, or find where it cannot be matched.
- **Do:** run `grid_t2.py` with `ROI=1` on the four tuning clips with x264 at 500, 1000, 1500, 3000 and 6000 kbit/s (`roi_t2.bat`). Arms: plain-resolution ROI with centre boxes 10, 30 and 50% and qoffsets -0.2, -0.4 and -0.6; method plus ROI on the three best T2 settings with qoffsets -0.2 and -0.4. Same boxes and metrics as T2; summary prints a head-to-head line per method setting. The NVENC route is T5; until it is measured, ROI is a CPU-only comparison.
- **Pass rule (written before the run):** on at least 3 of 4 tuning clips at 1500 and at 3000 kbit/s, some method or method plus ROI setting beats the best ROI arm whose whole-frame VMAF is within 1 of it by at least 3 VMAF in the centre-50% box.
- **If it fails:** ROI matches the method on x264. The claim narrows to encoders and services that ignore ROI and to starvation depths where the QP cap stops ROI. NVENC is not one of them in OBS 30.1 and later (see T5). Run T5 before deciding the pitch.
- **Smooth-falloff ROI arm (added 2026-11-17):** 3 to 4 nested centre boxes with qoffsets stepping toward 0 at the outer box, so the QP has no hard edge. This is the strongest version of the rival and must be beaten, not only hard-box ROI. Narrow rule: if it comes within 2 VMAF of the method in the centre-50 box at 1500 and 3000 kbit/s on 3 of 4 tuning clips with equal or better whole-frame VMAF, the claim narrows to encoders and links without ROI input. Check first that x264 applies overlapping addroi regions in the intended order.
- **Hypothesis to test, not a result:** ROI keeps spending bits on every edge pixel, while squish removes pixels; at deep starvation the quantizer cap should limit ROI first.
- **Smooth-falloff result (2026-11-18, CBR x264, 60 fps, four tuning clips, 1500 and 3000 kbit/s):** the narrow rule was read as fixed in `research/next-round/results/t12-smooth-cbr-60fps/plan.md`, written before the run. Box order checked first: passed. Result: 1 of 4 clips narrow at 1500 kbit/s and 0 of 4 at 3000 kbit/s. The rule needed 3 of 4 at both rates, so the claim does **not** narrow on these clips. The smooth arm comes within 2 VMAF of the method in centre-50 on several clips, but loses whole-frame VMAF. Files: `results.md` and `results.jsonl` in the same folder. The capped-VBR run is exploratory and not used. Tuning clips only; not held-out.

## What to report at the end of every task

A table with all arms and clips, the pass rule and whether it passed, the JSON files behind it, and one sentence on what the result changes in the evidence sheet.
