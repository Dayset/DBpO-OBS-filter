#!/usr/bin/env bash
# Unit and reference tests for directional-object-blur. Run from anywhere.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
src="$here/../src"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

gcc -O2 -Wall -Wextra -std=c17 -I"$src" "$src/camera-model.c" "$here/test_camera_model.c" -lm -o "$out/test_camera_model"
gcc -O2 -Wall -Wextra -std=c17 -I"$src" "$src/mouse-sync.c" "$here/test_mouse_sync.c" -lm -o "$out/test_mouse_sync"
"$out/test_camera_model"
"$out/test_mouse_sync"
python3 "$here/reference_blur.py"
