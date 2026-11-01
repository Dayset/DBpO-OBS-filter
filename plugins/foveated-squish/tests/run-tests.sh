#!/usr/bin/env bash
# Tests for foveated-squish and the viewer extension that unfolds it. Run from anywhere.
#   run-tests.sh          math tests: C plugin + JS extension vs the Python reference
#   run-tests.sh --e2e    also plays a squished video through the extension in headless Chromium
#                         (needs Python playwright + its Chromium)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$here/../../.."
src="$here/../src"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

gcc -O2 -Wall -Wextra -std=c17 -I"$src" "$src/squish-math.c" "$here/test_squish_math.c" -lm -o "$out/test_squish_math"
"$out/test_squish_math" >"$out/c.txt"
python3 "$here/compare_reference.py" "C plugin" <"$out/c.txt"

if command -v node >/dev/null; then
	node "$root/viewer/unsquish-extension/tests/compare.js" >"$out/js.txt"
	python3 "$here/compare_reference.py" "JS extension" <"$out/js.txt"
else
	echo "node not found: skipping the extension math check"
fi

if [[ "${1:-}" == "--e2e" ]]; then
	python3 "$root/viewer/unsquish-extension/tests/e2e.py"
fi
