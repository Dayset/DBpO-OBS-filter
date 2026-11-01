#!/usr/bin/env bash
# Tests for perceptual-color-reduction. Run from anywhere.
#   run-tests.sh            unit tests + quick bitrate check (CI)
#   run-tests.sh --full     full bitrate/CIEDE2000 report (slow)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
src="$here/../src"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

gcc -O2 -Wall -Wextra -std=c17 -I"$src" "$src/palette.c" "$here/test_palette.c" -lm -o "$out/test_palette"
"$out/test_palette"

if [[ "${1:-}" == "--full" ]]; then
	python3 "$here/bitrate_harness.py"
else
	python3 "$here/bitrate_harness.py" --check
fi
