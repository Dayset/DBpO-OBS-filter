"""Blinded pairwise viewer test at 1500 kbit/s (matched starvation). Trials are rendered at 1920x1080,
H.264 CRF 14, each trial = clip A (2 s) then clip B (2 s), labels A/B. The key is written separately.

  viewer_test.py arms  REF WORK WINDOW_IDX   -> WORK/viewer/arms_w<i>.npz (uint8 clips per arm)
  viewer_test.py trials WORK OUTDIR          -> OUTDIR/trial_XX.mp4 and OUTDIR/key_private.csv (not for viewers)
"""
import csv, json, os, random, subprocess, sys
import numpy as np, cv2, torch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import real_restore as rr
import eval6000_models as ev
from center_mask_eval import compose

N = 906
ARMS = ["baseline_plain", "float_unsharp025", "float_centermasked", "float_nounsharp"]
WINDOWS = [0, 2, 3]
OUT_W, OUT_H = 1920, 1080

def make_arms(ref_path, work, idx):
    torch.set_num_threads(2)
    ref = rr.mm(ref_path, (N, rr.H, rr.W, 3))
    unsq = rr.mm(f"{work}/unsq.u8", (N, rr.H, rr.W, 3)); plain = rr.mm(f"{work}/plain.u8", (N, rr.H, rr.W, 3))
    s = rr.windows(work)[0][idx]; e = s + rr.WIN
    sq = unsq[s:e].astype(np.float32) / 255
    f = ev.load(work, "f1500.pt", "float"); r = ev.run(f, sq)
    out = {
        "baseline_plain": plain[s:e],
        "float_unsharp025": (ev.unsharp(r, 0.25) * 255).round().astype(np.uint8),
        "float_centermasked": (compose(r, sq) * 255).round().astype(np.uint8),
        "float_nounsharp": (r * 255).round().astype(np.uint8),
    }
    os.makedirs(f"{work}/viewer", exist_ok=True)
    np.savez(f"{work}/viewer/arms_w{idx}.npz", **out)
    print("arms done window", idx, s, flush=True)

def upscale_label(frame, label):
    im = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), (OUT_W, OUT_H), interpolation=cv2.INTER_CUBIC)
    cv2.putText(im, label, (40, 90), cv2.FONT_HERSHEY_SIMPLEX, 2.5, (0, 255, 255), 5, cv2.LINE_AA)
    return im

def render_trials(work, outdir):
    os.makedirs(outdir, exist_ok=True)
    rng = random.Random(7)
    trials = []
    for w in WINDOWS:
        d = np.load(f"{work}/viewer/arms_w{w}.npz")
        for arm in ARMS[1:]:
            for swapped in (False, True):
                trials.append({"window": w, "arm": arm, "swapped": swapped})
    rng.shuffle(trials)
    key = []
    for n, t in enumerate(trials, 1):
        d = np.load(f"{work}/viewer/arms_w{t['window']}.npz")
        a, b = ("baseline_plain", t["arm"]) if not t["swapped"] else (t["arm"], "baseline_plain")
        name = f"trial_{n:02d}.mp4"
        p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OUT_W}x{OUT_H}",
                              "-r", "60", "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "14",
                              "-pix_fmt", "yuv420p", f"{outdir}/{name}"], stdin=subprocess.PIPE)
        for label, arm_key in (("A", a), ("B", b)):
            clip = d[arm_key]
            for fr in clip:
                im = upscale_label(fr, label)
                p.stdin.write(im.tobytes())
        p.stdin.close(); p.wait()
        key.append({"trial": n, "file": name, "window": t["window"], "A": a, "B": b, "compared": t["arm"]})
        print("rendered", name, flush=True)
    with open(f"{outdir}/key_private.csv", "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(key[0].keys()))
        wr.writeheader(); wr.writerows(key)

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "arms": make_arms(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    elif cmd == "trials": render_trials(sys.argv[2], sys.argv[3])
