#!/usr/bin/env bash
# Tests for foveated-squish and the viewer extension that unfolds it. Run from anywhere.
#   run-tests.sh          math tests: C plugin + JS extension vs the Python reference
#   run-tests.sh --e2e    also plays squished video through the extension in headless Chromium
#                         (e2e.py: picture accuracy; e2e_atlas.py: sharp regions; extension.py: popup, live apply, controls;
#                          look.py: viewer look vs a numpy model of the NVIDIA-style passes)
#                         (needs Python playwright + its Chromium)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$here/../../.."
src="$here/../src"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

python3 "$root/research/foveation/color_math.py" --golden-header | diff -q - "$here/look_golden.h" >/dev/null || { echo "tests/look_golden.h is out of date: regenerate with color_math.py --golden-header"; exit 1; }
gcc -O2 -Wall -Wextra -std=c17 -I"$src" -I"$here" "$src/squish-math.c" "$here/test_squish_math.c" -lm -o "$out/test_squish_math"
gcc -O2 -Wall -Wextra -std=c17 -I"$src" -I"$here" "$src/atlas-math.c" "$src/squish-math.c" "$here/test_atlas_math.c" -lm -o "$out/test_atlas_math"
python3 "$root/research/foveation/color_math.py" >/dev/null && echo "colour look reference selftest: PASS"
python3 "$root/research/foveation/atlas_math.py" --selftest >/dev/null && echo "atlas Python reference selftest: PASS"
"$out/test_atlas_math"
"$out/test_squish_math" >"$out/c.txt"
python3 "$here/compare_reference.py" "C plugin" <"$out/c.txt"

if command -v node >/dev/null; then
	node "$root/viewer/unsquish-extension/tests/look_code.js"
	node "$root/viewer/unsquish-extension/tests/compare.js" >"$out/js.txt"
	python3 "$here/compare_reference.py" "JS extension" <"$out/js.txt"
else
	echo "node not found: skipping the extension math check"
fi

if [[ "${1:-}" == "--e2e" ]]; then
	python3 "$root/viewer/unsquish-extension/tests/e2e.py"
	python3 "$root/viewer/unsquish-extension/tests/e2e_atlas.py"
	python3 "$root/viewer/unsquish-extension/tests/e2e_userscript.py"
	python3 "$root/viewer/unsquish-extension/tests/color.py"
	(cd "$root" && python3 viewer/unsquish-extension/tests/extension.py)
	python3 "$root/viewer/unsquish-extension/tests/look.py"
fi
