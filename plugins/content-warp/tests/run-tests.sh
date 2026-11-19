#!/usr/bin/env bash
# Tests for content-warp: CPU warp map.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
src="$here/../src"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

gcc -O2 -Wall -Wextra -std=c17 -I"$src" "$src/warp-math.c" "$here/test_warp_math.c" -lm -o "$out/test_warp_math"
"$out/test_warp_math"
