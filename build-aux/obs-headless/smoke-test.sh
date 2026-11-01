#!/usr/bin/env bash
# Runs every plugin's filter inside real libobs (OpenGL backend, virtual X
# display, software GPU) on synthetic frames and fails on any OBS render
# warning, shader compile error, missing output frame or black output.
#
# Requires: libobs-dev, libx11-dev, Xvfb, cmake, ninja, python3 + numpy.
#   build-aux/obs-headless/smoke-test.sh [build-dir]
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
build="${1:-$root/build_headless}"
work="$(mktemp -d)"
trap 'rm -rf "$work"; [[ -n "${xvfb_pid:-}" ]] && kill "$xvfb_pid" 2>/dev/null || true' EXIT

cmake -S "$root" -B "$build" -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_INSTALL_PREFIX="$build/install" >/dev/null
cmake --build "$build" >/dev/null
rm -rf "$build/install"
cmake --install "$build" >/dev/null
gcc -O2 -Wall -std=gnu17 -I/usr/include/obs "$root/build-aux/obs-headless/obs-headless.c" -lobs -lX11 -lpthread \
	-o "$work/obs-headless"

display=":$((90 + RANDOM % 9))"
Xvfb "$display" -screen 0 640x480x24 >/dev/null 2>&1 &
xvfb_pid=$!
sleep 1

W=320 H=180 N=12
python3 - "$work/in.rgba" "$W" "$H" "$N" <<'EOF'
import sys, numpy as np
path, w, h, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
rng = np.random.default_rng(1)
y, x = np.mgrid[0:h, 0:w]
with open(path, "wb") as f:
    for i in range(n):
        img = np.zeros((h, w, 4), np.uint8)
        img[..., 0] = x * 255 // w
        img[..., 1] = y * 255 // h
        img[..., 2] = 96
        img[..., 3] = 255
        img[40:80, 20 + 8 * i:60 + 8 * i, :3] = (230, 60, 40)  # moving box
        img[..., :3] = np.clip(img[..., :3].astype(int) + rng.integers(-3, 4, (h, w, 3)), 0, 255)
        f.write(img.tobytes())
EOF

status=0
for dir in "$root"/plugins/*/; do
	name="$(basename "$dir")"
	so="$(find "$build/plugins/$name" -maxdepth 1 -name "$name.so" | head -1)"
	data="$build/install/share/obs/obs-plugins/$name"
	# Optional per-plugin environment, e.g. OBS_HEADLESS_CANVAS=240x136 for a filter that fills the canvas.
	read -r -a extra_env <<<"$(cat "$dir/tests/smoke-env" 2>/dev/null || true)"
	ids="$(grep -ho '\.id = "[a-z0-9_]*"' "$dir"src/*.c | sed 's/.*"\(.*\)"/\1/')"
	for id in $ids; do
		if env "${extra_env[@]}" DISPLAY="$display" timeout 300 "$work/obs-headless" "$so" "$data" "$id" "$W" "$H" \
			<"$work/in.rgba" >"$work/out.rgba" 2>"$work/log.txt"; then
			size="$(grep -o 'output [0-9]*x[0-9]*' "$work/log.txt" | head -1 | cut -d' ' -f2)"
			result="$(python3 - "$work/in.rgba" "$work/out.rgba" "$W" "$H" "$N" "$size" <<'EOF'
import sys, numpy as np
a = np.fromfile(sys.argv[1], np.uint8); b = np.fromfile(sys.argv[2], np.uint8)
w, h, n = int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
ow, oh = (int(v) for v in sys.argv[6].split("x"))
if b.size != ow * oh * 4 * n:
    print(f"FAIL frames: got {b.size // (ow*oh*4)} of {n}"); sys.exit()
if (ow, oh) != (w, h):  # resizing filter: check it produced a real picture
    b = b.reshape(n, oh, ow, 4)
    print(("FAIL output is black" if b[..., :3].max() == 0 else "ok") + f" resized to {ow}x{oh}"); sys.exit()
a = a.reshape(n, h, w, 4).astype(int); b = b.reshape(n, h, w, 4).astype(int)
if b[..., :3].max() == 0:
    print("FAIL output is black"); sys.exit()
d = np.abs(a[..., :3] - b[..., :3]).mean()
print(("FAIL" if d > 40 else "ok") + f" mean change {d:.2f}")
EOF
)"
		else
			result="FAIL ($(grep -E 'error|warning|counted|failed' "$work/log.txt" | head -3 | tr '\n' ' '))"
		fi
		echo "$name / $id: $result"
		[[ "$result" == ok* ]] || status=1
	done
done
exit $status
