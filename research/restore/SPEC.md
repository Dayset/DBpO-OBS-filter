# Edge restorer — spec (issue #21, epic #20)

Viewer-side, per-frame network that restores edge detail on a starved picture. Runs in the extension / userscript after the stream frame is decoded and the level is read from the marker (`research/foveation/STARVATION_SPEC.md`).

## 1. Scope (v1)

- **Plain levels only:** 720 (code 1), 540 (code 2), 360 (code 3). Squish levels (codes 4–5) are out of scope until a later issue, because their picture is non-uniform.
- **Periphery only when a squish mask is present** is a later issue. v1 restores the whole picture.

## 2. Shapes

The picture is the `rect2(a, 1920, 1080)` rectangle from `starvation_math.py`, and the restorer output fills the viewer's full picture size. Source is the decoded picture region.

| Level | Source picture (input) | Output (full picture) | Scale `s` |
|---|---|---|---|
| 720 (code 1) | 1280×720 | 1920×1080 | 1.5 |
| 540 (code 2) | 960×540 | 1920×1080 | 2 |
| 360 (code 3) | 640×360 | 1920×1080 | 3 |

Notes:
- `s` need not be an integer. The pixel-shuffle path needs an integer `s`, so for 720 the network runs at source size and the output is produced by a bilinear sampling of the pixel-shuffled grid to 1920×1080. The reference (`restore_math.py`) and viewer must match this exactly. For v1 we fix the rule: **pixel-shuffle to `ceil(s)`, then area-resample to the output size.** This is a v1 choice, not a claim about quality.
- Input and output are RGB, HWC, float32 in [0, 1] (the viewer converts from 8-bit once).
- The viewer's bicubic upscale is the residual base, so the network learns only the correction.

## 3. Ops (must be expressible as WebGL2 fragment passes or WGSL compute)

1. Bicubic upscale of the input to output size (the residual base).
2. Conv 3×3, zero padding 1, bias, no stride. Input channels 3 → C, then C → C, C → C, each followed by ReLU.
3. Conv 3×3 C → 3·k² (k = ceil(s)), no activation.
4. Pixel shuffle with factor k (channel index `c·k² + i·k + j` → output `(c, h·k + i, w·k + j)`).
5. Area resample to output size (only when `s` is not an integer; a no-op for s = 2, 3).
6. Add the bicubic base, clip to [0, 1].

Post-network step (separate and switchable, not part of the weights): **unsharp mask** `out + amt·(out − gauss(out, σ=1.5))`, amount 0.6–1.0 default 1.0. The network does not learn it.

Width `C`: 20 for the 10k class; other sizes in #25.

## 4. Weight file format (v1)

Little-endian binary, one file per model:

```
bytes 0..3    magic  b"RSTR"
bytes 4..7    uint32 version = 1
bytes 8..11   uint32 header_len (bytes)
bytes 12..    header_len bytes of UTF-8 JSON
then          raw tensor data, each tensor at its byte offset from the start of the data region
```

JSON header:

```json
{
  "width": 20,
  "tensors": [
    {"name": "conv1.w", "dtype": "f32", "shape": [20, 3, 3, 3],  "offset": 0,     "nbytes": 2160, "scale": 1.0},
    {"name": "conv1.b", "dtype": "f32", "shape": [20],           "offset": 2160,  "nbytes": 80,   "scale": 1.0}
  ]
}
```

- `dtype` is one of `f32`, `i8`.
- For `i8`, the real value is `q · scale`, where `scale` is a per-tensor float. Biases are always `f32`.
- **Ternary** weights are stored as `i8` with values in {−1, 0, +1} and a per-layer `scale` (the BitNet-style `s = mean|w|` from the prototype). The loader does not need to know it is ternary.
- Tensor names: `conv1`–`conv4` with `.w` / `.b`. A `meta.s` tensor is not used; `s` comes from the level, not the file.

Export: `state_dict` → this format is a function in `restore_math.py` (`export_state_dict(state, path, dtype)`), added in #21.2 (see its issue).

## 5. Acceptance (from #21)

- [ ] This file (SPEC.md) committed.
- [ ] Reference forward pass matches the golden vector (`restore_golden.json`) within 1e-6.
- [ ] The viewer implementation (#26.1) must reproduce the same golden output within 1/255.
