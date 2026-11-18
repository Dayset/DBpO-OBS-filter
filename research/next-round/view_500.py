"""Save plain x264 and squish56 x264 at one bitrate as separate viewable files (no CNN, bilinear unsquish).

  python view_500.py REF.mkv OUT_DIR [KBPS=500]
"""
import os, subprocess, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stack"))
import stack_bench as sb

ref, out = sys.argv[1], sys.argv[2]
kbps = int(sys.argv[3]) if len(sys.argv) > 3 else 500
W, H, SQ = 1920, 1080, (1440, 810)
os.environ.setdefault("CODEC", "x264")
os.makedirs(out, exist_ok=True)

plain = os.path.join(out, f"plain_x264_{kbps}.mkv")
sb.encode(ref, plain, kbps)

sq = sb.Squish(W, H, *SQ)
src = os.path.join(out, "_src_squish.mkv")
SQW, SQH = SQ
dec_ref = subprocess.Popen([sb.FF, "-v", "error", "-i", ref, "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
w = subprocess.Popen([sb.FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{SQW}x{SQH}", "-r", "60",
                      "-i", "-", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", "-r", "60", src], stdin=subprocess.PIPE)
while True:
    b = dec_ref.stdout.read(W * H * 3)
    if len(b) < W * H * 3: break
    f = np.frombuffer(b, np.uint8).reshape(H, W, 3)
    w.stdin.write(np.ascontiguousarray(sq.fwd(f)).tobytes())
w.stdin.close(); assert w.wait() == 0
sqmkv = os.path.join(out, "_sq.mkv")
sb.encode(src, sqmkv, kbps)

restored = os.path.join(out, "_restored.mkv")
p = subprocess.Popen([sb.FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "60",
                      "-i", "-", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", restored], stdin=subprocess.PIPE)
dec = subprocess.Popen([sb.FF, "-v", "error", "-i", sqmkv, "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       stdout=subprocess.PIPE)
while True:
    b = dec.stdout.read(SQW * SQH * 3)
    if len(b) < SQW * SQH * 3: break
    f = np.frombuffer(b, np.uint8).reshape(SQH, SQW, 3)
    p.stdin.write(np.ascontiguousarray(sq.inv(f)).tobytes())
p.stdin.close(); assert p.wait() == 0
viewer_mkv = os.path.join(out, f"squish56_x264_{kbps}.mkv")
sb.encode(restored, viewer_mkv, kbps)
viewer = os.path.join(out, f"squish56_x264_{kbps}.mp4")
subprocess.run([sb.FF, "-v", "error", "-y", "-i", viewer_mkv, "-c", "copy", "-movflags", "+faststart", viewer], check=True)
os.remove(restored); os.remove(viewer_mkv)
os.remove(src); os.remove(sqmkv)
plain_mp4 = os.path.join(out, f"plain_x264_{kbps}.mp4")
subprocess.run([sb.FF, "-v", "error", "-y", "-i", plain, "-c", "copy", "-movflags", "+faststart", plain_mp4], check=True)
os.remove(plain)
# downscale arm: same resolution path as squish's transmitted size (1440x810), bicubic back to 1080p, one encode at kbps
down_up = os.path.join(out, "_down_up.mkv")
subprocess.run([sb.FF, "-v", "error", "-y", "-i", ref, "-vf", "fps=60,scale=1440:810:flags=bicubic,scale=1920:1080:flags=bicubic",
                "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", "-an", down_up], check=True)
scale_mkv = os.path.join(out, f"scale810_x264_{kbps}.mkv")
sb.encode(down_up, scale_mkv, kbps)
scale_mp4 = os.path.join(out, f"scale810_x264_{kbps}.mp4")
subprocess.run([sb.FF, "-v", "error", "-y", "-i", scale_mkv, "-c", "copy", "-movflags", "+faststart", scale_mp4], check=True)
os.remove(down_up); os.remove(scale_mkv)
print("saved:", plain_mp4, viewer, scale_mp4)
