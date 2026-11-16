# Round 3: test the whole stack, not one part (2026-11-16)

Rules 1 to 11 of `PLAN.md` stay in force.

**Rule 12.** Every table carries a row named `stack` (all components that are ready) and one row per component removed.
A component that does not move the `stack` row is cut. The plain downscale is one of our own rungs, not the competitor.
The baseline to beat is "best of plain and downscale per 2 s segment", which a platform can do today.

## What the 15 November PC run showed (7 clips, 4 encoders, 3000 and 6000 kbit/s)

- Squish against the same-pixel downscale, 56 cells: centre PSNR higher in 56 (+0.6 to +8.3 dB, mean +2.5), VMAF p5
  higher in 40, mean VMAF higher in 22.
- Squish reaches the centre PSNR of plain 1080p at 6000 at about 3200 to 3300 kbit/s on every encoder. Whole-frame VMAF
  is about 11 lower there.
- Restorer (leave-one-clip-out, h264_nvenc, 6000, 7 clips): squish + cnn_masked 62.8 VMAF against downscale + cnn 61.9;
  p5 51.8 against 50.3; centre 26.0 against 23.7 dB.
- Unsharp raises VMAF v0.6.1 while lowering centre PSNR (cnn+usm50: +7.5 VMAF, -1.5 dB centre).
- Halving the bitrate moved every encoder toward squish (squish minus plain VMAF, 6000 to 3000: h264_nvenc +3.1 to +4.4,
  hevc_nvenc -0.4 to +0.9, av1_nvenc -1.0 to -0.2, x264 +4.1 to +4.8). Nothing below 3000 has been run on HEVC or AV1.

## Faults in the current lab runner (R1 fixes them)

1. Per-frame `.npz` names carry no codec, so each codec overwrote the last; only x264 per-frame data survives.
2. One clip is 128 frames (2 s); rate control never settles. Refuse clips under 10 s.
3. At 3000, plain got more than 5% more bits than squish in 5 of 28 cells. Measure bitrate over the scored frames.
4. Two bitrates give no rate curve.

## Tasks

| Task | Proves | Runs on | Done when |
|---|---|---|---|
| R1 Harness fix and rate curves | Numbers convert to bitrate saved | PC | Codec in every `.npz` name; clips under 10 s refused; bitrate measured over the scored frames; 750, 1000, 1500, 2250, 3000, 4500, 6000, 9000 kbit/s on all four encoders; BD-rate per codec with the spread over clips; the 540p spin at 1500 re-run with the control arm |
| R2 Metrics | The claim is measured where it is made | cloud code, PC run | Centre-crop VMAF (30% and 50% boxes), gaze-weighted PSNR, VMAF-NEG, harmonic mean and p5 in every result file; gaze desk research from E5 done |
| R3 CNN restorer, full | The restorer result is not one clip | PC | `cnn_sweep.py` finished for all clips, three NVENC codecs and x264, 3000 and 6000; both arms get their CNN; one net across bitrates tested against per-bitrate nets |
| R4 Stack and ablation | The parts add up | PC | `stack` row plus leave-one-out rows for flattening, blur, emphasis, CNN, unsharp, on the R1 grid |
| R5 NVENC emphasis map | GPU ROI baseline and the centre boost | PC | QP-delta map reaches NVENC (`addroi` is ignored); centre PSNR moves more than 0.3 dB; `roi` and `squish+roi` arms in the grid |
| R6 Controller, three rungs | "Only when needed" with our own downscale rung | cloud, on R1 arrays | 90% of the three-rung oracle on every clip; never more than 1 VMAF under the best static rung; thresholds fitted on half the clips, tested on the rest |
| R7 Blind test, redesigned (replaces S8's pairs) | How much bitrate a sharper centre is worth to viewers | people | Protocol committed first; pairs at equal bitrate (stack against control) and at equal centre (stack at 3000 against plain and control at 6000); result is the bitrate at which viewers are indifferent |
| R8 Cost model | The money claim | owner plus agent | One real platform's or CDN's prices, bitrate ladder and transcode cost replace the published figures |
| R9 Sharpening-neutral pass | The winning restoration recipe is not a metric artifact | PC, after R3 | VMAF-NEG for bicubic, cnn_masked and the best unsharp recipe on every sweep cell; a recipe is rejected if it lowers centre PSNR by more than 0.3 dB against bicubic |
| R10 A 3:1 rung | Whether a stronger squeeze helps starved segments | cloud code, PC run | A 33% pixel rung tested on starved segments only, in the R1 grid. No other change to the warp curve or filter without the owner's say |
| R11 Envelope study (pilot for R7) | Where viewers stop accepting the picture | people | 10 viewers, 15 s per condition, blind, rating 0 none, 1 visible, 2 annoying; each condition twice, two anchor conditions four times; grid of bitrate by rung; one green, yellow, red map |
| R12 White paper | The method stands on its own for a technical reviewer | agent, after R1 to R4 | Abstract with one number and its condition, method with the squish equations and an original, squeezed, restored figure, architecture, study design, results, discussion, limits; every number from a committed JSON |

Order: R1 first; it blocks R4 and R6. R2 and R5 run beside it. R3 is running on the PC. R9 follows R3. R7 needs R4.
S4 to S7 of `PLAN.md` are unchanged.

Stop and report: after R3 and R4, if the `stack` row does not beat "best of plain and downscale" by 1 VMAF-NEG or
1.5 dB centre at equal bitrate on HEVC and AV1 at any bitrate of the R1 grid, the claim narrows to H.264 and starved links.
