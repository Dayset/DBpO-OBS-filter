# Bitrate x picture size x squeeze sweep (issue #18)

Self-contained: this folder runs without the rest of the repo (`vendor/` holds a copy of `starvation_math.py`,
`atlas_math.py`, `squish_math.py` and the golden vectors; `python vendor.py` refreshes it).

## Run it (Windows 11)

1. Install once: `winget install Gyan.FFmpeg` and `winget install Python.Python.3.12` (open a new terminal after).
2. Put clips in `sources\<class>\` — the folder name is the source class:

   ```
   sources\busy_action\ps2_fight.mkv
   sources\fast_pan\racing.mkv
   sources\static_ui\inventory.mkv
   sources\text_scroll\chat_scroll.mkv
   sources\talking_head\webcam.mkv
   sources\mixed_hud\minimap_game.mkv
   ```

   1920x1080, 60 fps, at least 20 s, lossless or >= 50 Mbit/s (OBS: Recording format MKV, encoder
   "Lossless" or NVENC CQP 16). Other sizes/rates are converted and flagged.
3. Double-click `run_sweep.bat`. First time it builds `.venv`, checks everything, plans the Stage 1 grid,
   prints the time estimate and runs. Or from a terminal:

   ```
   run_quick4.bat               short check first (double-click): busiest 4 s of each clip, 3000/6000 kbit/s,
                                sizes 1080p..540p, squeeze, the interlace step, x264 + NVENC (~2 min per clip)
   run_sweep.bat --quick        smoke test (~10 min), then
   run_sweep.bat                Stage 1 (--full); continues the same results folder
   run_sweep.bat --stage2       refinement + NVENC spot check, planned from Stage 1 results
   ```

**Stop and resume:** Ctrl+C, close the window, reboot, power cut — then run it again. Every finished run is
in `results\results.csv` (fsynced before the next one is recorded); at most the runs in progress are lost.
The PC is kept awake while it runs, at below-normal priority (`--priority normal` to change).

## Commands

```
python dbpo_sweep.py check  [--sources DIR]       ffmpeg, x264/NVENC, golden self-test, disk, source checks
python dbpo_sweep.py plan   [--quick|--full|--stage2] [grid options]   adds runs to results\plan.json
python dbpo_sweep.py run    [--jobs N]            runs everything pending, then writes the report
python dbpo_sweep.py status                       done / failed / pending + time left
python dbpo_sweep.py report                       best.csv + summary.md from what is there
python dbpo_sweep.py auto   [plan options]        check -> plan (if new options or no plan) -> run -> report
```

Any grid: `--a 0:63:1` (every size step), `--q 0:7`, `--bitrate 500,1000,8000` or `500:8000:500`,
`--encoder x264,nvenc` (or `x264:medium`, `nvenc:p5`), `--preset veryfast,medium`, `--pack field,split`
(interlace step at full size, `none` = off; every plan includes both by default), `--excerpt 20`
(0 = whole clip), `--excerpt-at busiest|middle|start|<s>`, `--only ps2,racing`. Plans only ever add runs;
everything goes into the same results folder (`--out` for a separate study). Failed runs are recorded with the
error and retried on the next start (`--skip-failed` to leave them).

Defaults for this PC (16 cores): `--jobs 5`, `--x264-threads 6` (fixed, so results don't depend on `--jobs`),
`--nvenc-parallel 2`. Stage 1 is ~216 runs of 10 s; expect roughly 15-30 minutes. Every size step
(`--a 0:63:1 --q 0,7`, 6 bitrates, 6 sources) is ~4,500 runs, a night.

## Output (`results\`)

| file | what |
|---|---|
| `results.csv` | one row per run: the agreed schema, then `q_req, excerpt_start_s, excerpt_s, rand_centre_ssim, rand_whole_ssim, runtime_s, status, error, run_id, finished_at` |
| `frames.csv` | every sampled frame of every run (step / random), for re-analysis |
| `best.csv` | per source x encoder x bitrate: best (a, q) on centre and on whole SSIM, gain over 1080p, H1/H3 per cell |
| `summary.md` | H1-H7 table, best picture height per source and bitrate, interlace step vs 1080p and vs the best size, flagged cells, runtime |
| `manifest.json` | sources with sha256, excerpts, sampled frame indices, complexity, ffmpeg/Python versions, vendored commit |
| `plan.json`, `sources.json`, `cache\` | the plan, probe/hash cache, reference luma of the sampled frames (memmap) |

## What exactly is measured

- **Stream:** 1920x1080 frame, picture top-left at step `a` (`rect2`), black elsewhere, marker v2 last
  (also at `a=0`). The picture is `starvation_math.render2` with the base config `--squish-cfg`
  (default `rx=ry=1, cx=cy=0.15, linear`): `q=0` is the even area shrink, `q>0` keeps the centre 30% denser
  and takes the shrink from the edges.
- **Squeeze clamp:** `q` is limited per `a` by the density floor 0.08 (`max_q`). With the default config the
  most `q` can be is 7 at a<=16, 6 at 25, 4 at 32, 3 at 38/44, 2 at 50, 1 at 57/63. The row stores the
  effective `q` and the asked `q_req`; duplicates are not run twice, and `a=0` has no squeeze variants (so
  Stage 1 is 216 runs, not 240). H3 compares the strongest allowed `q` with `q=0`. `--floor` or a different
  `--squish-cfg` (smaller `cx`, curve `px`) gives the squeeze more room.
- **Encode:** RGB -> BT.709 limited 4:2:0, CBR with maxrate = bufsize = target, keyframe every 2 s, 2 B-frames.
  x264: `nal-hrd=cbr`. NVENC: `p7 -tune hq -rc cbr -multipass fullres` (`--nvenc-args` for AQ etc.).
- **Viewer:** decode, read the marker, crop the rect, bilinear unsquish (`view2`), luma only.
- **Interlace step (`pack` column, issue #19):** at full size the picture goes out one field per frame, parity
  alternating, vertical anti-twitter [1 2 1]/4 first. `field`: the field in the top half, black below.
  `split`: even fields stay in the top half and odd fields in the bottom half; each frame replaces only its
  own half, so the static half costs nothing. Viewer: ffmpeg `bwdif` on the received fields (one field of
  look-ahead). No marker in these runs (`marker_pct` empty). H7 = the interlace step is at least as sharp in
  the centre as the best picture size. Results from 1.0.0 are kept; the file gets the new `pack` column once.
- **Reference:** the source luma through the same BT.709 limited conversion, so only coding + resampling loss
  is measured.
- **Frames:** every 4th frame of the excerpt plus 3 random ones (seed 7, reported separately as `rand_*`).
- **Metrics:** SSIM (7x7 box window) and PSNR on centre (30% x 30%), edge (outside the centre 60% x 60%) and
  whole frame; the 160x32 marker strip is excluded from edge and whole. Run values are means over the frames
  (PSNR = mean of per-frame PSNR). `marker_pct` counts frames whose decoded (a, q) equals the sent one.
  `mbit_s` comes from the packet sizes.
- **Complexity:** temporal = mean 240x135 frame difference (the detector's metric), spatial = mean Sobel
  gradient magnitude of the luma; both on the excerpt actually used. The default excerpt is the busiest 10 s.
- **Stage 2:** per source x bitrate, the two best non-zero sizes from the 10 s x264 runs plus 1080p, at 20 s,
  `q` in {0, 2, 4, 7}; and NVENC P7 on the Stage 1 size grid at `q=0` on the same 10 s excerpts (H4).
