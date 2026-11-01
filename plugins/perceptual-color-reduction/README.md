# Perceptual Color Reduction

An OBS Studio filter that prepares the picture for the video encoder, working in **OKLab** (a perceptually uniform color space): edge-preserving noise flattening, chroma smoothing, temporal denoise, and optional color reduction (OKLab posterize, adaptive octree or k-means palette) with ordered dithering.

**Goal: less bandwidth at the same perceived quality.** Every option was measured against x264 bitrate and CIEDE2000 color error before choosing defaults (below). The headline finding inverts the original brief: **reducing the number of colors raises bitrate**; flattening noise is what saves it.

## What the defaults do

Only **Flatten noise = 50** (edge-preserving smoothing, range 0.02 OKLab, 5×5). It was the only option that lowered bitrate on every clip type, and the real plugin running inside OBS reproduces the model's numbers (see *Verification*).

## Measurements

`tests/bitrate_harness.py` runs synthetic game-like clips (960×540, 60 frames @ 60 fps) through a numpy model of the shader, using the plugin's own C palette code, encodes with **x264 veryfast** at **CRF 18** (high quality, e.g. recording or generous stream bitrate) and **CRF 23** (typical), and compares bitrate and CIEDE2000 against the unprocessed clip.

Bitrate change vs. original (CRF 18 / CRF 23) and mean ΔE2000:

| Variant | Clean pan | Grainy pan | Static, grainy | Smooth gradients | Mean ΔE2000 |
|---|---|---|---|---|---|
| **Flatten 0.02 (default)** | **−4.9% / −2.0%** | **−56.3% / −2.6%** | **−67.0% / +1.4%** | **−0.8% / −0.4%** | 0.02–1.83 |
| Chroma smoothing σ 1.5 | −0.3% / −3.1% | +72.0% / −1.9% | +72.9% / +3.0% | −1.3% / −5.0% | 0.18–2.23 |
| Temporal denoise 0.03 | +6.0% / +1.6% | +1.9% / +0.2% | −34.2% / +2.9% | +4.4% / +1.6% | 0.22–1.05 |
| Hold unchanged pixels 0.010 | +0.5% / −1.9% | +4.9% / +0.5% | −1.4% / −1.1% | +84.0% / +23.6% | 0.13–0.36 |
| Posterize L96/C32 + hold | +21.5% / +12.1% | +55.0% / +5.6% | +52.4% / +8.3% | +38.3% / +27.5% | 1.8–2.1 |
| Posterize L48/C16 | +42.5% / +28.4% | +602% / +84.7% | +648% / +151% | +21.3% / +22.8% | 3.9–4.2 |
| Posterize L32/C12 + Bayer 4×4 | +180% / +100% | +560% / +141% | +749% / +146% | +260% / +207% | 6.6–7.1 |
| Octree 128 colors | +46.0% / +17.7% | +342% / +28.5% | +240% / +68.8%¹ | +104% / +47.4%¹ | 0.8–2.9 |
| K-means 128 colors + hold | +178% / +36.8% | — | +119% / +25.3% | +108% / +48.0% | 1.0–2.8 |

¹ with hold. Flatten strength sweep (0.01 → 0.04): savings level off above 0.02 (grainy pan −41.9% → −58.3% at CRF 18) while ΔE keeps rising (1.01 → 1.99), so 0.02 is the default.

**Why color reduction costs bits:** encoders already represent smooth gradients and low-amplitude noise cheaply (a few low-frequency coefficients, or nothing at all after quantization). Quantizing colors replaces them with hard contour edges, which need many high-frequency coefficients, and contours that move with the camera must be re-encoded every frame. Dithering adds a fixed high-frequency pattern on top. Flattening does the opposite: it removes the fine noise the encoder would otherwise spend bits on, while keeping edges.

At CRF 23 most savings shrink to a few percent because x264 already discards grain at that quality. At a **fixed streaming bitrate** the effect shows up as quality instead: bits not spent on grain go to real detail.

### The brief's hypotheses, measured

| Hypothesis | Result |
|---|---|
| Perceptual color reduction saves 15–20% bitrate | **Not supported.** Posterize +21% to +1100%, palettes +7% to +340% across clips and quality levels. |
| Dithering breaks up banding without hurting compression | **Not supported.** Bayer 4×4 roughly doubles to octuples bitrate. |
| Mean CIEDE2000 < 5 is achievable | Yes for palettes (0.8–2.9), posterize L48/C16 (3.9–4.2), L96/C32 (1.8–2.1). Posterize L32/C12 is just over (5.1–5.8). |
| Octree is inherently more temporally stable than k-means | **Not on its own.** Rebuilding an octree on the same scene with new noise moves output colors by 0.0019 OKLab on average; warm-started k-means 0.0010; octree with palette matching + smoothing 0.0006. All are under 1/10 of a just-noticeable difference (~0.02). |
| ≤ 2 ms GPU cost | Not measured on a real GPU here. The frame is converted to OKLab once (16-bit float), so smoothing is 25 texture reads per pixel; the palette LUT (64³ entries) is rebuilt only when the palette changes. Check **Stats → render time** in OBS. |

## How it works

| Pass | Resolution | What it does |
|---|---|---|
| ToLab | full | Converts the frame to OKLab once (16-bit float) |
| Process | full | Edge-preserving smoothing (weights fall off with OKLab distance), chroma smoothing, quantization (OKLab grid or palette LUT), Bayer dither, temporal denoise / hold against the previous output |
| Sample + CPU | 160×90 | Read back one frame late (no GPU stall); octree (`palette_octree`) or warm-started k-means (`palette_kmeans`) in OKLab, palette matching + smoothing between updates (`palette_stabilize`), scene-change detection |
| BuildLUT | 512×512 | 64³ RGB grid → nearest palette color, packed 8×8 tiles; rebuilt only when the palette changes |
| Compare | full | Optional split screen (left original, right result) |

Palette timings on the 2-core test VM: octree 0.5 ms, warm k-means update 3.7 ms (128 colors, 14,400 samples), done every *N* frames on the render thread.

## Settings

- **Bandwidth**
  - **Flatten noise** — edge-preserving smoothing strength (50 = 0.02 OKLab, the measured sweet spot).
  - **Chroma smoothing** — helps smooth gradients, costs bits on grainy footage (it leaves the grain in lightness, which is what the encoder's luma channel sees).
  - **Temporal denoise** — motion-adaptive recursive filter; only helps static grainy scenes.
  - **Hold unchanged pixels** — keeps last frame's pixel while the source stays close; costs bits on slow pans.
- **Edge softening** (off by default) — foveated low-pass: the center stays sharp, blur ramps up toward the sides (and optionally top/bottom). Viewers need nothing special. Measured: +0.1% to −2.0% bitrate at CRF 23, −1.1% to −13.8% at CRF 18; at a fixed stream bitrate it does *not* make the center sharper (see `research/foveation`). *View → Softened zone* tints the affected area.
- **Color reduction** (off by default) — *Posterize* (OKLab grid: lightness and chroma levels), *Adaptive palette: octree* or *k-means* (colors, update every N frames, palette smoothing, scene-change threshold), *Luminance protection* (weights lightness in palette distances), *Dither* (none / Bayer 4×4 / 8×8 and strength).
- **View** — result, or split compare.

## Verification

- `tests/test_palette.c` — OKLab round trip (worst error 1.9e-5), octree recovers exact colors, accuracy and timing, palette shimmer limits.
- `tests/bitrate_harness.py` — the measurements above; `--check` (CI) asserts flatten saves >10% and posterize costs bits on the grainy clip; `--sweep` for strength sweeps.
- **Real plugin vs. model:** running the built plugin inside libobs (`build-aux/obs-headless`) on the grainy clip matched the numpy model within 0.26/255 on average and gave the same saving (−56.1% at CRF 18 for both). Posterize, octree and k-means modes ran inside OBS with no render warnings and reduced 6,190 source colors to 36–57.

**Measure your own footage:** record 10–20 s of gameplay losslessly (OBS: *Recording Format* MKV, *Encoder* x264 with *CRF 0*, or Lossless), then

```
python3 tests/bitrate_harness.py --clip gameplay.mkv --frames 300
```

Synthetic clips are a proxy: real game content, NVENC/AV1 encoders and CBR rate control can shift these numbers.
