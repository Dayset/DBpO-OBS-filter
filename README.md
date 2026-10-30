# DBpO-OBS-filter — Directional Object Blur

An OBS Studio filter that estimates motion between frames and applies **directional motion blur per region**, so each moving object blurs along its own direction and speed while static areas stay sharp.

Target: **Windows 11, OBS Studio 31+ (Direct3D 11)**.

## How it works

All passes run on the GPU as pixel shaders (`data/directional_object_blur.effect`):

| Pass | Resolution | What it does |
|---|---|---|
| Downsample | 1/2, 1/4 or 1/8 | Box-filters the frame for motion analysis |
| Flow | flow-res | Block-matching optical flow against the previous frame (3×3 SAD patches, ±N search) |
| Coherence | flow-res | Cross-bilateral smoothing: pixels of similar color share one motion vector — a lightweight "per-object" step until real segmentation lands |
| NeighborMax | flow-res | Spreads the strongest nearby motion so blur extends past object edges |
| Blur | full-res | Gathers samples along the dominant motion, weighting each by whether its own motion reaches this pixel |

The Blur pass follows the reconstruction filter from McGuire et al., *A Reconstruction Filter for Plausible Motion Blur* (I3D 2012), minus depth, with optical flow standing in for a game's velocity buffer.

Expect a one-frame delay: motion is always measured between frame N−1 and N.

## Settings

- **Strength (shutter)** — blur length as a fraction of per-frame motion. 0.5 ≈ 180° film shutter.
- **Max blur radius** — caps blur length in pixels.
- **Object sensitivity** — higher keeps differently-colored objects from sharing motion.
- **Blur samples** — quality vs. cost of the final gather.
- **Advanced** — analysis resolution, search radius (max detectable motion = radius × downscale px/frame), coherence radius, minimum motion, static bias (noise rejection).
- **View → Motion vectors (debug)** — hue = direction, brightness = speed.

## Building (Windows 11)

Requirements: Visual Studio 2022 (Desktop C++ workload), CMake 3.28+, Git.

```powershell
cmake --preset windows-x64
cmake --build --preset windows-x64 --config RelWithDebInfo
```

The first configure downloads OBS sources and dependencies (pinned in `buildspec.json`).

## Installing

Download the **windows-x64** artifact from the latest GitHub Actions run and unzip it so you end up with exactly this layout:

```
C:\ProgramData\obs-studio\plugins\directional-object-blur\
├── bin\64bit\directional-object-blur.dll
└── data\
    ├── directional_object_blur.effect
    └── locale\en-US.ini
```

Remove any older copy of `directional-object-blur.dll` from `C:\Program Files\obs-studio\obs-plugins\64bit\`, then restart OBS. The filter appears under **Effect Filters → Directional Object Blur**.

The shader is also compiled into the DLL, so the filter works even if `data\` is missing; a `.effect` file in `data\` takes priority, which makes live shader tweaking possible.

GitHub Actions builds Windows, macOS and Linux packages on every push (`.github/workflows`).

## Roadmap

1. ✅ Block-matching flow + velocity-driven directional blur
2. Coarse-to-fine (pyramid) flow for larger motions at low cost
3. Real per-object masks (e.g. from a background-removal source) to replace color coherence
4. Optional ONNX optical-flow model
5. Temporal smoothing of vectors to reduce flicker

Built on [obs-plugintemplate](https://github.com/obsproject/obs-plugintemplate).
