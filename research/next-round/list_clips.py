"""T6 helper: one line per clip with length, size, motion label and sha256, for CLIPS.md.
  python list_clips.py OUT.csv CLIP1 [CLIP2 ...]
Motion label = mean absolute difference between consecutive frames at 480x270 grey (0 to 255)."""
import csv, hashlib, subprocess, sys
import numpy as np
rows = []
for p in sys.argv[2:]:
    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration", "-of", "csv=p=0", p], capture_output=True, text=True).stdout.split()
    wh, dur = pr[0].split(","), float(pr[1])
    d = subprocess.Popen(["ffmpeg", "-v", "error", "-i", p, "-vf", "fps=60,scale=480:270:flags=area,format=gray", "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    prev, acc, n = None, 0.0, 0
    while True:
        b = d.stdout.read(480 * 270)
        if len(b) < 480 * 270: break
        f = np.frombuffer(b, np.uint8).astype(np.float32)
        if prev is not None: acc += float(np.abs(f - prev).mean()); n += 1
        prev = f
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 24), b""): h.update(chunk)
    rows.append([p, round(dur, 1), f"{wh[0]}x{wh[1]}", round(acc / max(n, 1), 3), h.hexdigest()])
    print(rows[-1], flush=True)
with open(sys.argv[1], "w", newline="") as f:
    w = csv.writer(f); w.writerow(["path", "seconds", "size", "motion", "sha256"]); w.writerows(rows)
