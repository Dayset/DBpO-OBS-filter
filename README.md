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
| StaticMask + UIDilate | flow-res | Counts how long each spot stays identical; spots unchanged while the scene moves are screen UI, protected from blur (grown by a margin) |
| FitSample + CPU fit | 64×36 | Reads back a tiny copy of the motion field and robustly fits a camera model (pan, zoom, rotation, shear), ignoring moving objects and HUD as outliers |
| Combine | flow-res | Final motion = camera motion × camera strength + object motion (what the camera model doesn't explain) × object strength |
| JFAInit + JFAStep | flow-res | Velocity dilation by jump flooding: every pixel inside some moving pixel's blur trail learns that pixel's blur vector, out to the full blur length in log₂(length) passes, so blur extends past silhouettes |
| Blur | full-res | For each time slice of the exposure, decides whether a moving thing covered this pixel (its motion reaches here, or ours reaches it) and averages; trailing or centered shutter, optional tail fade and highlight weighting |
| Effects + TrailMask | full-res / flow-res | Optional non-directional blur and glow on moving objects, and trails via frame feedback |
| Present | full-res | Opacity mix with the original |

Motion blur here is reconstructed the way a camera exposure works: per time slice, a pixel shows a moving object if that object's own motion carried it over the pixel, otherwise its own color. Velocities are first dilated with the jump flood algorithm so trails reach their full length beyond object silhouettes (as proposed in the video *How I Solved Real Time Motion Blur*, building on McGuire et al., *A Reconstruction Filter for Plausible Motion Blur*, I3D 2012). By default the blur **trails** the object like a real shutter instead of being centered on it, so nothing smears ahead of a moving object.

`tests/reference_blur.py` is a numpy model of the shader compared against a true box-shutter exposure of a moving object; it runs in CI. At 12–90 px/frame the reconstruction closes ~92–94 % of the gap between the sharp frame and the real exposure (the previous centered neighbor-max method: 13–33 %).

Without look-ahead, motion is measured between frame N−1 and N and blur is applied to frame N. With **Look-ahead** set to L frames, the filter outputs frame N−L, so it knows the motion both into and out of every frame it blurs.

## Settings

- **Object blur strength** — blur length for moving objects, as a fraction of their per-frame motion (0.5 = 180° film shutter, 1.0 = 360°, up to 20× for stylized smears).
- **Cinematic shutter**
  - **Blur placement** — 0 = trailing (real camera: the smear ends at the object's current position), 100 = centered (typical game motion blur, smears ahead too). With look-ahead, the camera model and motion blending center on the matching point of the exposure.
  - **Tail fade** — older parts of the exposure fade out, for a comet-like streak.
  - **Highlight streaks** — bright samples weigh more, so lights and muzzle flashes keep their intensity when smeared, like on film.
- **Opacity** — mix of the effect over the original frame.
- **Max blur radius** — caps blur length in pixels (up to 512).
- **Object sensitivity** — higher keeps differently-colored objects from sharing motion.
- **Blur samples** — quality vs. cost of the final gather (up to 64; per-pixel jitter hides banding).
- **Camera motion** (checkable group) — fills in motion across the whole frame from a fitted camera model, so moving forward in a 3D game blurs as a zoom even on flat walls and sky.
  - **Camera blur strength** — separate from object strength.
  - **Camera motion smoothing** — damps jitter in the fitted model.
  - **Object threshold** — how far (px) a region must move against the camera to count as an object.
  - **Blur starts at / Full blur at (px/frame)** and **Response curve** — speed-to-blur mapping for camera motion: no blur below the first speed, full strength from the second, curve shapes the ramp (<1 = strong early, >1 = only fast swings).
- **Protect static UI** (on by default) — HUD, minimap frame and menus that stay identical while the scene moves are never blurred, and scene blur stops pulling their colors in. *Sensitivity* trades how much change is tolerated / how quickly a region counts as UI; *margin* grows the protected area. Check with **View → Protected UI**.
- **Moving object effects**
  - **Object blur type** — directional (along motion), non-directional (soften by speed), or both.
  - **Glow** — light from moving objects spreads outward (screen blend), with adjustable radius.
  - **Trails** — keep a fading copy of previous frames where objects moved (or everywhere, including camera motion). Trail length sets how slowly it fades.
- **Raw mouse input (camera turns)** — opt-in, Windows only. Reads relative mouse movement (no clicks, wheel or keys) via Windows Raw Input inside OBS's own process; it never touches the game. The filter learns counts → on-screen pixels per axis (sensitivity, FOV, inversion) and the input-to-capture latency from frames where image analysis is reliable, then uses the mouse for turns too fast to measure. The mouse is only used once it has agreed with the picture (≥50 % on-screen agreement), so wrong manual values or menus don't cause blur; in manual mode the automatic estimate is still shown as a reference. *Fast turns only* (default) or *All camera pan*; automatic or manual calibration. Progress is shown in the settings and logged every ~10 s.
- **Look-ahead (frames)** — 0–6. Delays video by that many frames (6 = 100 ms at 60 fps) to average the camera model across frames before *and* after the output frame (smooth without lag) and to blend motion into and out of each frame (cleaner edges where objects appear or disappear). The settings show the exact delay; add the same **Sync Offset** to your audio sources in *Edit → Advanced Audio Properties*. Uses one extra full-res frame of GPU memory per frame of look-ahead (about 15 MB each at 1440p).
- **Center clear spot** — keeps the middle of the frame sharp (e.g. your crosshair/aim point). *Oval* follows the frame's proportions; *Square* is square in pixels. Size is a percentage of frame height; edge softness blends the spot into the blur.
- **Advanced** — analysis resolution, search radius (max detectable motion = radius × downscale px/frame), coherence radius, minimum motion, static bias (noise rejection), dither (off by default: per-pixel grain hides banding in long blurs but costs encoder bitrate).
- **View** — result, or debug overlays (hue = direction, brightness = speed) for the final motion, camera motion only, object motion only, the protected UI mask, or the **dilated blur reach** (where trails will land).

## Streaming / encoder notes

Motion blur removes fine detail in moving areas, which is exactly what costs an encoder the most bits, and trails increase frame-to-frame similarity. Things that work against the encoder: per-pixel grain (keep **Dither** off), flickering motion estimates (use **Look-ahead** and **Camera motion smoothing**), and very strong glow on noisy scenes.

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
1. ✅ Camera-motion model (pan/zoom/rotation) with separate camera/object strengths
1. ✅ Optional look-ahead (0–6 frames) and center clear spot
1. ✅ Raw mouse input with auto-calibration; opacity, trails, non-directional blur and glow per moving object
1. ✅ Static UI protection and speed-to-blur response curve
1. ✅ Jump-flood velocity dilation, physically based trailing-shutter reconstruction, tail fade, highlight streaks
1. Manual exclusion zones (e.g. minimap interior, which changes with the view)
2. Coarse-to-fine (pyramid) flow for larger motions at low cost
3. Real per-object masks (e.g. from a background-removal source) to replace color coherence
4. Optional ONNX optical-flow model

Built on [obs-plugintemplate](https://github.com/obsproject/obs-plugintemplate).
