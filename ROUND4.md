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

Order: T0, then T1 and T6 in parallel, then T2, then T3 to T5 in parallel, then T7, then T8. T9 can run at any time.

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
- **Pass rule:** a restoration arm improves outside-50% VMAF-NEG by at least 2 without lowering centre-30% PSNR by more than 0.3 dB.

### T5. Encoder emphasis on GPU
- **Why:** ffmpeg's `addroi` is ignored by NVENC (byte-identical output); x264 honours it (+4.65 dB centre). Platforms use GPU encoders.
- **Do:** read the NVENC SDK and ffmpeg source to find how a QP-delta or emphasis map can be passed; build the smallest working path; arms `roi` and `method + roi`.
- **Pass rule:** centre-30% PSNR moves by more than 0.3 dB on h264_nvenc, with the cost outside the centre reported.
- **Stop and report if:** no path exists without patching the driver-side SDK; then ROI stays a CPU-only comparison.

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

## What to report at the end of every task

A table with all arms and clips, the pass rule and whether it passed, the JSON files behind it, and one sentence on what the result changes in the evidence sheet.
