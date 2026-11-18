"""Each arm is one CBR encode of what it transmits at kbps, then decoded back to 1080p and saved as a
crf 12 mp4 for viewing and for centre_vmaf.py. The bitrate is matched on the transmitted stream, not on the viewing file.

  python arms_decode.py REF.mkv OUT_DIR [KBPS=6000]
Arms:
  plain     : 1920x1080 stream
  squish56  : 1440x810 squish stream, bilinear unsquish on decode
  scale810  : 1440x810 stream, bicubic upscale on decode
"""
import os, subprocess, sys
import cv2, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stack"))
import stack_bench as sb

ref, out = os.path.abspath(sys.argv[1]), sys.argv[2]
kbps = int(sys.argv[3]) if len(sys.argv) > 3 else 6000
W, H, SW, SH = 1920, 1080, 1440, 810
os.environ.setdefault("CODEC", "x264")
os.makedirs(out, exist_ok=True)
sq = sb.Squish(W, H, SW, SH)

def write(frames, path, w, h, qp=None):
    args = ["-c:v", "libx264", "-qp", str(qp)] if qp is not None else ["-c:v", "libx264", "-preset", "slow", "-crf", "12"]
    p = subprocess.Popen([sb.FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", "60",
                          "-i", "-"] + args + ["-pix_fmt", "yuv420p", "-an", path], stdin=subprocess.PIPE)
    for f in frames: p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close(); assert p.wait() == 0

def decode(path, w, h):
    dec = subprocess.Popen([sb.FF, "-v", "error", "-i", path, "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
    n = w * h * 3
    while True:
        b = dec.stdout.read(n)
        if len(b) < n: break
        yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
    dec.wait()

def ref_frames(): return decode(ref, W, H)

tmp = lambda name: os.path.join(out, name)

# plain
s = tmp(f"_plain_{kbps}.mkv"); sb.encode(ref, s, kbps)
write(decode(s, W, H), tmp(f"plain_x264_{kbps}.mp4"), W, H); os.remove(s)

# squish56
src = tmp("_sq_src.mkv"); write((sq.fwd(f) for f in ref_frames()), src, SW, SH, qp=0)
s = tmp(f"_sq_{kbps}.mkv"); sb.encode(src, s, kbps)
write((sq.inv(f) for f in decode(s, SW, SH)), tmp(f"squish56_x264_{kbps}.mp4"), W, H); os.remove(s); os.remove(src)

# scale810
src = tmp("_sc_src.mkv")
subprocess.run([sb.FF, "-v", "error", "-y", "-i", ref, "-vf", f"scale={SW}:{SH}:flags=bicubic", "-c:v", "libx264", "-qp", "0",
                "-pix_fmt", "yuv420p", "-an", src], check=True)
s = tmp(f"_sc_{kbps}.mkv"); sb.encode(src, s, kbps)
write((cv2.resize(f, (W, H), interpolation=cv2.INTER_CUBIC) for f in decode(s, SW, SH)), tmp(f"scale810_x264_{kbps}.mp4"), W, H)
os.remove(s); os.remove(src)
print("saved:", *[tmp(n) for n in (f"plain_x264_{kbps}.mp4", f"squish56_x264_{kbps}.mp4", f"scale810_x264_{kbps}.mp4")])
