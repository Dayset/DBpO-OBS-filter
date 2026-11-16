"""Writes research/stack/module_timing.json: best of 3 single-thread runs per mode. usage: timing.py in1080.y4m in1440.y4m"""
import json, re, subprocess, sys
B = "/home/claude/dbpo-obs-filter/stack/libsquish/dbpo-squish"
def run(args, src):
    best = 1e9
    for _ in range(3):
        r = subprocess.run([B, "-t"] + args, stdin=open(src, "rb"), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        best = min(best, float(re.search(r", ([\d.]+) ms", r.stderr).group(1)))
    return round(best, 2)
a, b = sys.argv[1:3]
subprocess.run(f"{B} 1440 810 < {a} > /tmp/sq/sq810.y4m", shell=True)
out = {"note": "ms per frame, filter only, one thread, best of 3 on the 2-core sandbox (not the owner's PC); dbpo-squish -O3, v3 clip",
       "1080p_to_1440x810": {"area": run(["1440", "810"], a), "point": run(["-p", "1440", "810"], a),
                             "unsquish_bilinear_810_to_1080": run(["-u", "1920", "1080"], "/tmp/sq/sq810.y4m")},
       "1440p_to_1920x1080": {"area": run(["1920", "1080"], b), "point": run(["-p", "1920", "1080"], b)},
       "python_area_reference_ms": 40, "opencv_remap_point_ms": 6.9, "x264_saving_ms_per_frame": 16}
json.dump(out, open("/home/claude/dbpo-obs-filter/research/stack/module_timing.json", "w"), indent=1); print(json.dumps(out, indent=1))
