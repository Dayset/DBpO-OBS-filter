"""VMAF on the centre box only, or VMAF-NEG on the whole frame.

  python centre_vmaf.py DISTORTED REFERENCE [BOX=0.3] [neg]

BOX is the centred square's side as a fraction of the frame (0.3 = centre 30%, 0.5 = centre 50%).
Both inputs are scaled to 1920x1080 (area) and forced to 60 fps, the same way stack_bench builds its reference.
Writes DISTORTED.<tag>.vmaf.json next to DISTORTED and prints the pooled mean. Needs ffmpeg with libvmaf.
"""
import json, os, subprocess, sys
dist, ref = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
box = float(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] != "neg" else 1.0
neg = "neg" in sys.argv[3:]
W, H = 1920, 1080
cw, ch = int(W * box) // 2 * 2, int(H * box) // 2 * 2
x, y = (W - cw) // 2, (H - ch) // 2
tag = ("neg" if neg else "vmaf") + ("" if box == 1.0 else f".centre{int(round(box * 100))}")
out_dir = os.path.dirname(dist)
log = f"{os.path.splitext(dist)[0]}.{tag}.json"
base = f"scale={W}:{H}:flags=area,fps=60,format=yuv420p,crop={cw}:{ch}:{x}:{y}"
model = "model=version=vmaf_v0.6.1neg:" if neg else ""
graph = f"[0:v]{base}[d];[1:v]{base}[r];[d][r]libvmaf={model}log_fmt=json:log_path={os.path.basename(log)}"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", dist, "-i", ref, "-lavfi", graph, "-f", "null", "-"],
               check=True, cwd=out_dir)
pooled = json.load(open(log))["pooled_metrics"]["vmaf"]["mean"]
print(f"{os.path.basename(dist)} {tag}: {pooled:.2f}  ({log})")
