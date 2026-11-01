# DBpO OBS Plugins

A suite of OBS Studio filters aimed at **streaming: lower bandwidth and a cleaner picture for the encoder** at the same perceived quality. Each plugin is independent: install only the ones you want.

Target: **Windows 11, OBS Studio 31+ (Direct3D 11)**. Linux builds are produced too; macOS is not built (see below).

| Plugin | What it does | Docs |
|---|---|---|
| **Directional Object Blur** | Per-object and camera motion blur from optical flow, jump-flood velocity dilation, trailing shutter, raw mouse input for fast turns, UI protection | [plugins/directional-object-blur](plugins/directional-object-blur/README.md) |
| **Perceptual Color Reduction** | Perceptual color/noise reduction in OKLab, measured against encoder bitrate | [plugins/perceptual-color-reduction](plugins/perceptual-color-reduction/README.md) |

## Installing (Windows)

Download the **windows-x64** artifact from the latest successful [GitHub Actions run](../../actions). It contains one folder per plugin. Copy the folder(s) you want into `C:\ProgramData\obs-studio\plugins\`, so you end up with e.g.

```
C:\ProgramData\obs-studio\plugins\
├── directional-object-blur\
│   ├── bin\64bit\directional-object-blur.dll
│   └── data\…
└── perceptual-color-reduction\
    ├── bin\64bit\perceptual-color-reduction.dll
    └── data\…
```

Restart OBS. The filters appear under **Effect Filters**. Remove any older copy of a plugin's DLL from `C:\Program Files\obs-studio\obs-plugins\64bit\` first.

## Building

Requirements (Windows): Visual Studio 2022 (Desktop C++ workload), CMake 3.28+, Git.

```powershell
cmake --preset windows-x64
cmake --build --preset windows-x64 --config RelWithDebInfo
```

The first configure downloads OBS sources and dependencies (pinned in `buildspec.json`). On Linux, `libobs-dev` is enough for a local build.

## Repository layout

```
plugins/<name>/          one folder per plugin
  CMakeLists.txt         project(<name> VERSION …) + add_obs_plugin(…)
  src/  data/            sources; data/ ships next to the DLL (shaders, locale)
  tests/run-tests.sh     run by CI for every plugin
  README.md
common/                  shared sources (logging with per-plugin prefix)
research/                measurements that aren't plugins (yet), e.g. research/foveation
cmake/common/obs_plugin.cmake   add_obs_plugin(), target_embed_text()
build-aux/effect-to-hlsl.py     lets CI compile every .effect with Microsoft's FXC
```

### Adding a plugin

1. Create `plugins/<name>/CMakeLists.txt`:
   ```cmake
   project(<name> VERSION 0.1.0 LANGUAGES C)
   add_obs_plugin(${PROJECT_NAME} SOURCES src/plugin-main.c src/<filter>.c)
   target_embed_text(${PROJECT_NAME} data/<shader>.effect <symbol> <header>.h)  # optional
   ```
2. Add `src/`, `data/locale/en-US.ini`, and `tests/run-tests.sh`.

The root `CMakeLists.txt` picks up every folder in `plugins/` automatically; CI compiles every `.effect` with FXC and runs every `tests/run-tests.sh`.

## CI

- **Build Project**: Windows and Ubuntu packages (one archive, one folder per plugin). macOS is disabled: the suite targets Windows (raw mouse input is Windows-only) and the template's macOS installer assumes one plugin per repository.
- **Shader Check**: compiles every shader entry point with FXC (`ps_4_0`/`vs_4_0`), as OBS does on Direct3D 11.
- **Unit Tests**: each plugin's `tests/run-tests.sh`.
- **Check Formatting**: clang-format 19 and gersemi.

Built on [obs-plugintemplate](https://github.com/obsproject/obs-plugintemplate). License: GPL-2.0.
