# Content Warp: handoff to finish the filter

Branch `feature/content-warp`, head `27ec610`. Review date 2026-11-18.

## Where it stands

| Symptom (user, OBS 32.2.2, D3D11, RTX 5080) | Cause | Status |
|---|---|---|
| OBS froze when the filter was added | `D3DCompile` unrolled the 240/128 loops while holding the graphics lock | Fixed in `7960772` (`[loop]`) |
| One source column stretched across each row | Warp map sampled as 0 | Partly explained, not fixed |
| No image at all on a 1-source scene | Draws skipped: log repeats `device_draw (D3D11): Not all shader parameters were set` | **Open** |

CI was already reporting the failure. The OBS Headless Smoke Test has been red on every content-warp commit, with:

```
content-warp / content_warp_filter settings panel: FAIL (controls without a hint)
content-warp / content_warp_filter: FAIL (... Error compiling shader: )
```

So the effect does not compile on OpenGL at all, and on D3D11 it compiles but its draws get rejected. **Rule: do not hand a build to the user until the smoke test and the shader check are both green.**

## Problems found in review

1. **The shader is embedded as a C string.** Every other plugin keeps it in `data/*.effect`. Because this one doesn't, the FXC job (`shader-check.yaml`) never sees it, and compile errors are not logged: `gs_effect_create(..., NULL)` throws the error text away. If `f->fx` is NULL, render then does nothing without saying why.
2. **There is GLSL-incompatible syntax.** The C-style casts `(int)floor(...)` and `(int)ceil(...)` don't work under libobs' HLSL→GLSL translation, and the `[loop]` attribute probably doesn't either. This is the likely reason the smoke test reports `Error compiling shader`.
3. **The warp map is built on the GPU with per-pixel 240-step searches** (`src_u`, which runs twice per map pixel). It's slow to compile, fragile across backends, and impossible to unit-test. The map only changes every N frames and is just 240 numbers, so the CPU should build it.
4. **"Not all shader parameters were set" (D3D11).** libobs throws this when a uniform that a pass uses has no value at draw time. It isn't yet known which uniform. Removing the Map pass, as in step 3 below, shrinks the set of suspects to the Render pass.
5. **No property has a hint.** That fails the smoke test's settings-panel check.
6. **`OBS_SOURCE_SRGB` is declared but not handled.** Either drop the flag or follow `squish-filter.c`.

## Steps (do them in order, one commit each, and push after each)

### 1. Move the shader to a file
- Create `plugins/content-warp/data/content_warp.effect` from the string in `content-warp.c`.
- In `CMakeLists.txt`, add `target_embed_text(${PROJECT_NAME} data/content_warp.effect cw_embedded_effect embedded-effect.h)`, the same as `plugins/foveated-squish/CMakeLists.txt`.
- Load it the way `squish-filter.c` does around line 584: `obs_module_file("content_warp.effect")` → `gs_effect_create_from_file(path, &errors)`. Fall back to the embedded copy if the file is missing.
- **Log `errors` with `blog(LOG_ERROR, ...)`.** In `cw_video_render`, if `f->fx == NULL`, call `obs_source_skip_video_filter` and return.
- Check: the Shader Check (FXC) workflow now runs on this effect and passes.

### 2. Build the warp map on the CPU
- New file `src/warp-math.c/.h` with a pure function:
  `void cw_build_map(const float energy[240], float p, float ew, float srcW, float *map_out, int map_w);`
  It should be a direct port of `PSMap` and `src_u`: prefix sums over `energy + 0.02` for the left bins `[0, floor(L*240))` and the right bins `[ceil(R*240), 240)`, an inverse lookup for each output x, and a linear centre.
- Add `plugins/content-warp/tests/test_warp_math.c` and `tests/run-tests.sh`, following `plugins/foveated-squish/tests/test_squish_math.c`. Test these cases:
  - Flat energy gives a map that is monotonic and evenly spaced on each side.
  - The centre segment has slope `1/srcW` in u per canvas pixel (1:1).
  - `map[0] == 0` and `map[map_w-1]` is about 1.
  - A spike of energy in one bin gives that bin more output pixels.
- The Unit Tests workflow runs `plugins/*/tests/run-tests.sh` automatically.

### 3. Feed the map from a double-buffered readback
- Keep the Energy pass. Use a constant loop bound with no attributes, and cut it to 32 rows if 128 is slow. Render it into the 240×1 `GS_RGBA32F` texrender.
- Stage it with `gs_stage_texture` into one of two `gs_stagesurf_t` and map the other, so reads lag one cache period. Copy the pattern in `squish-filter.c`, lines 1085–1106. Note that the stage surface format must match: use `GS_R32F` or `GS_RGBA32F` for both the texrender and the stagesurface.
- Run `cw_build_map` on the CPU. Upload the result with `gs_texture_set_image` into a `map_w`×1 `GS_R32F` texture created once with `GS_DYNAMIC`.
- Delete the `Map` technique, `src_u` and `e_at` from the effect. The `Render` pixel shader then only needs `image`, `warp`, `srcW`, `ew` and `mode_a`. Set all five right before `gs_effect_loop(f->fx, "Render")`, on every frame.
- Until the first readback arrives, skip the Render pass and draw the source unwarped, so the user never sees black.

### 4. Fix GLSL compatibility in what's left
- Use no C-style casts. Write `int(x)` and `float(x)`, which are valid in both HLSL and libobs GLSL.
- Use no `[loop]` or `[unroll]` attributes.
- Check: the Smoke Test no longer prints `Error compiling shader` for content-warp.

### 5. Hints and colour space
- Call `obs_property_set_long_description` on the `mode`, `centre` and `cache` properties, using the same pattern as `squish-filter.c` line 2020.
- Drop `OBS_SOURCE_SRGB` from `output_flags`, unless you copy squish's sRGB handling.
- Check: the settings panel line in the smoke test says `ok`.

### 6. Debug view, for the user
- Add a "View" list with four options: Final, Source (no warp), Energy (grey bars), Map (grey ramp). If the user reports "no image" again, ask which view fails. That points straight at the stage that breaks.

### 7. Hand back to the user
- All three workflows must be green on the head commit: OBS Headless Smoke Test, Shader Check, and Unit Tests. Then dispatch the build (`gh workflow run dispatch.yaml --repo Dayset/DBpO-OBS-filter --ref feature/content-warp -f job=build`) and wait for the Windows job to pass.
- Have the user test on a scene with a single Browser or Game Capture source:
  - Mode B (16:9 with pillarbox): the picture appears, with black bars, a sharp centre, and squeezed sides.
  - Mode A: set the canvas to 4:3 and stretch the source.
  - The log must not contain `Not all shader parameters were set`.
- The user wants unfinished work completed without asking for confirmation. Report only what remains.

## Known setup items (not code)
- The user's `Scene` still holds a `Gaussian Frame Hold` filter whose DLL isn't installed, so OBS logs "Source ID not found" on every start. Tell the user to remove it or to install the gauss-frame-hold artifact.
- The repo moved to `Dayset/DBpO-OBS-filter`. The local `origin` URL still works through the redirect.
