"""R1 harness: rate curves per codec, fixed from the four faults in ROUND3.md.

  python -I r1_runner.py WORK REF.mkv [CODECS=h264_nvenc,hevc_nvenc,av1_nvenc,x264]

Fixes:
  1. codec is in every output name (WORK/<codec>_<arm>_<kbps>.npz, one result JSON per codec)
  2. clips under 10 s are refused before any encode
  3. bitrate is measured over the scored frames only (frames 60 onward, same window for every arm)
  4. eight rates: 750 1000 1500 2250 3000 4500 6000 9000 kbit/s
Arms: plain (full 1920x1080) and squish56 (1440x810 squish, the 56% pixel arm) and scale810 (plain downscale, the control).
Needs $VMAF_FFMPEG (libvmaf build). Run on the PC with the GPU, not in the sandbox.
"""
import json, os, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stack"))
import numpy as np
import stack_bench as sb

RATES = [750, 1000, 1500, 2250, 3000, 4500, 6000, 9000]
if os.environ.get("QP_MODE"):  # fixed-QP sweep: rates are QP values, bitrate is measured
    RATES = [18, 22, 26, 30, 34, 38, 42]
if os.environ.get("RATES"):  # explicit list, e.g. RATES=500 for the rate-controlled low end
    RATES = [int(x) for x in os.environ["RATES"].split(",")]
ARMS = {"plain": ("plain",), "squish56": ("squish", "1440x810"), "scale810": ("scale", "1440x810")}
MIN_SECS = 10.0

work, ref = sys.argv[1], sys.argv[2]
codecs = (sys.argv[3] if len(sys.argv) > 3 else "h264_nvenc,hevc_nvenc,av1_nvenc,x264").split(",")

dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", ref],
                           capture_output=True, text=True, check=True).stdout)
if dur < MIN_SECS:
    sys.exit(f"REFUSED: {ref} is {dur:.1f} s; R1 needs at least {MIN_SECS:.0f} s")

for codec in codecs:
    os.environ["CODEC"] = codec  # stack_bench.encode() reads this at call time
    out_json = os.path.join(work, f"r1_{codec}.json")
    for arm_name, args in ARMS.items():
        for kbps in RATES:
            name = f"{codec}_{arm_name}"
            sb.arm(ref, work, out_json, name, kbps, *args)
    # fix 3: one bitrate definition for every arm, over frames 60 onward
    res = json.load(open(out_json))
    for key, r in res.items():
        d = np.load(os.path.join(work, key.replace("/", "_") + ".npz"))
        b = d["bytes"][60:]
        r["kbps_scored"] = round(float(b.sum()) * 8 / (len(b) / 60) / 1000, 1)
    json.dump(res, open(out_json, "w"), indent=1)
    print(codec, "done", flush=True)
