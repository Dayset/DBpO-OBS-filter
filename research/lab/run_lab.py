"""One-command lab run on the PC (tasks E1 and E4; the data never leaves the PC).

  python run_lab.py CLIPS_DIR WORK_DIR [SECONDS=20] [CODECS=h264_nvenc,hevc_nvenc,av1_nvenc,x264]

CLIPS_DIR holds the recordings (any ffmpeg-readable files, e.g. the NVIDIA app's AV1 1440p mp4). For every clip, and
for every codec, it makes the 1080p reference once and runs the plain, plain-downscale (810p), squish56 (bicubic
unsquish) and 2-zone ROI arms at 3000 and 6000 kbit/s, using research/stack/stack_bench.py. Resumable: finished arms
are skipped. Results are small: WORK_DIR/results/<clip>_<codec>.json and the per-frame .npz files in WORK_DIR/<clip>.
Needs: python with numpy scipy opencv-python, ffmpeg and ffprobe on PATH (an ffmpeg with libvmaf, or set VMAF_FFMPEG).
"""
import glob, os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__)); bench = os.path.join(here, "..", "stack", "stack_bench.py")
clips, work = sys.argv[1], sys.argv[2]
secs = sys.argv[3] if len(sys.argv) > 3 else "20"
codecs = (sys.argv[4] if len(sys.argv) > 4 else "h264_nvenc,hevc_nvenc,av1_nvenc,x264").split(",")
C = "x=iw*0.35:y=ih*0.35:w=iw*0.3:h=ih*0.3"; M = "x=iw*0.2:y=ih*0.2:w=iw*0.6:h=ih*0.6"
ROI2 = f"addroi={C}:qoffset=-0.3,addroi={M}:qoffset=-0.15"
os.makedirs(os.path.join(work, "results"), exist_ok=True)
for path in sorted(glob.glob(os.path.join(clips, "*.mp4")) + glob.glob(os.path.join(clips, "*.mkv"))):
    name = "".join(c if c.isalnum() else "_" for c in os.path.splitext(os.path.basename(path))[0])[-40:]
    d = os.path.join(work, name); os.makedirs(d, exist_ok=True); ref = os.path.join(d, "ref.mkv")
    if not os.path.exists(ref): subprocess.run([sys.executable, bench, "ref", path, ref, "1920", "1080", "0", secs], check=True)
    for codec in codecs:
        out = os.path.join(work, "results", f"{name}_{codec}.json"); env = dict(os.environ, CODEC=codec)
        for k in (6000, 3000):
            arms = [("plain", "plain"), ("scale810", "scale", "1440x810"), ("squish56", "squish", "1440x810"), ("roi2zone", "roi", ROI2)]
            for a, kind, *rest in arms:
                if codec != "x264" and kind == "roi" and "nvenc" not in codec: continue
                r = subprocess.run([sys.executable, bench, "arm", ref, d, out, a, str(k), kind, *rest], env=dict(env, UNSQ_CUBIC="1"))
                if r.returncode: print("FAILED", name, codec, a, k, flush=True)
print("done; send nothing: tell Claude to read", os.path.join(work, "results"))
