"""Real-gameplay edge restoration after Foveated Squish (PlanetSide 2 release clip, v3.0.0).

Pipeline (viewer path of the squish plugin, math from research/foveation/squish_math.py):
  ref 960x540 60 fps -> squish X+Y (r=0.75, center c=0.15, linear) to 720x404
  -> x264 veryfast CBR 1500 kbit/s -> decode -> unsquish (bilinear, viewer)  = "sq_bicubic"
  -> unsharp 1.0 (= "sq_unsharp") or ternary 10k restorer (= "sq_restorer").
  "plain" = ref 960x540 x264 CBR 1500 kbit/s, no squish (reference stream).

Training: frames 0-299 and 600-905 (the clip minus its middle 5-10 s). Test: four 2 s windows
(120 frames) inside frames 300-600, ranked by motion; the busiest is the headline.

Usage (each stage resumes / caches):
  real_restore.py prep  REF_RGB WORK
  real_restore.py train WORK STEPS
  real_restore.py eval  WORK WINDOW_INDEX      (0-3; writes WORK/eval_<i>.json)
  real_restore.py report WORK
"""
import json, os, subprocess, sys, time
import numpy as np, cv2, torch, torch.nn as nn, torch.nn.functional as F
from skimage.metrics import structural_similarity as ssim_fn

H, W, FPS = 540, 960, 60
SQ_H, SQ_W = 404, 720           # squished canvas (even dims for 4:2:0)
BUDGET = "1500k"                # 6 Mbit/s at 1080p scaled to 960x540 (1/4 pixels)
CENTER_C = 0.15                 # half-size of the sharp center, as the plugin default (30% total)
TRAIN = list(range(0, 300)) + list(range(600, 906))
TEST_RANGE = (300, 600)
WIN = 120                       # 2 s at 60 fps
C = 22                          # channels; ternary restorer ~10k params (see params count)

# ---------------------------------------------------------------- squish math (squish_math.Axis)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "foveation"))
from squish_math import Axis

def axis_for(n, m):
    return Axis(r=m / n, c=CENTER_C, f=0.5, p=1.0)

def area_matrix(n, m):
    ax = axis_for(n, m)
    ue = ax.forward(np.linspace(0, 1, n + 1)) * m
    j = np.arange(m)
    ov = np.clip(np.minimum((j + 1)[:, None], ue[None, 1:]) - np.maximum(j[:, None], ue[None, :-1]), 0, None)
    return (ov / ov.sum(1, keepdims=True)).astype(np.float32)

def unsquish_maps(n, m):
    ax = axis_for(n, m)
    pos = ax.forward((np.arange(n) + 0.5) / n) * m - 0.5
    return pos.astype(np.float32)

AX, AY = area_matrix(W, SQ_W), area_matrix(H, SQ_H)
UX, UY = unsquish_maps(W, SQ_W), unsquish_maps(H, SQ_H)
MAPX = np.tile(UX[None, :], (H, 1)); MAPY = np.tile(UY[:, None], (1, W))

def squish(img):  # (H,W,3) float32 -> (SQ_H,SQ_W,3)
    t = (AX @ img.transpose(1, 0, 2).reshape(W, H * 3)).reshape(SQ_W, H, 3).transpose(1, 0, 2)
    return (AY @ t.reshape(H, SQ_W * 3)).reshape(SQ_H, SQ_W, 3)

def unsquish(img):
    return cv2.remap(img, MAPX, MAPY, cv2.INTER_LINEAR)

# ---------------------------------------------------------------- stream
def cbr(frames_u8, w, h):
    enc = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS),
        "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-b:v", BUDGET, "-maxrate", BUDGET, "-bufsize", "3000k",
        "-g", "120", "-pix_fmt", "yuv420p", "-f", "matroska", "-"], input=np.ascontiguousarray(frames_u8).tobytes(),
        capture_output=True, check=True).stdout
    dec = subprocess.run(["ffmpeg", "-v", "error", "-i", "-", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        input=enc, capture_output=True, check=True).stdout
    return len(enc), np.frombuffer(dec, np.uint8).reshape(-1, h, w, 3)

# ---------------------------------------------------------------- ternary 10k restorer
class TernaryConv(nn.Conv2d):
    def forward(self, x):
        w = self.weight
        s = w.abs().mean().clamp(min=1e-5)
        wq = torch.clamp(torch.round(w / s), -1, 1) * s
        return F.conv2d(x, w + (wq - w).detach(), self.bias, self.stride, self.padding)

class Restorer(nn.Module):
    def __init__(self, c=C):
        super().__init__()
        self.body = nn.Sequential(
            TernaryConv(3, c, 3, padding=1), nn.ReLU(inplace=True),
            TernaryConv(c, c, 3, padding=1), nn.ReLU(inplace=True),
            TernaryConv(c, c, 3, padding=1), nn.ReLU(inplace=True),
            TernaryConv(c, 3, 3, padding=1))
    def forward(self, x):
        return x + self.body(x)

def sobel(x):  # (N,1,H,W)
    kx = torch.tensor([[[[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]]]], dtype=x.dtype) / 8
    ky = torch.tensor([[[[-1, -2, -1], [0, 0, 0], [1, 2, 1]]]], dtype=x.dtype) / 8
    gx, gy = F.conv2d(x, kx, padding=1), F.conv2d(x, ky, padding=1)
    return torch.sqrt(gx * gx + gy * gy + 1e-8)

# ---------------------------------------------------------------- stages
def mm(path, shape, mode="r"):
    return np.memmap(path, np.uint8, mode, shape=shape)

def stream_encode(frames, w, h, mkv):
    """frames: iterable of uint8 (h,w,3). Writes an x264 CBR mkv (streamed, bounded memory)."""
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
        "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-b:v", BUDGET, "-maxrate", BUDGET,
        "-bufsize", "3000k", "-g", "120", "-pix_fmt", "yuv420p", mkv], stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close(); p.wait()
    assert p.returncode == 0
    return os.path.getsize(mkv)

def stream_decode(mkv, w, h):
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", mkv, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE)
    fs = w * h * 3
    while True:
        b = p.stdout.read(fs)
        if len(b) < fs: break
        yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
    p.wait()

def prep(ref_path, work):
    os.makedirs(work, exist_ok=True)
    n = os.path.getsize(ref_path) // (H * W * 3)
    ref = mm(ref_path, (n, H, W, 3))
    if not os.path.exists(f"{work}/motion.npy"):
        mot = np.zeros(n, np.float32); prev = None
        for i in range(n):
            lum = ref[i].astype(np.float32).mean(2)
            if prev is not None: mot[i] = np.abs(lum - prev).mean()
            prev = lum
        np.save(f"{work}/motion.npy", mot)
    sq_mkv, pl_mkv = f"{work}/sq.mkv", f"{work}/plain.mkv"
    if not os.path.exists(f"{work}/sq.done"):
        stream_encode((np.clip(squish(ref[i].astype(np.float32) / 255) * 255 + 0.5, 0, 255).astype(np.uint8) for i in range(n)),
                      SQ_W, SQ_H, sq_mkv)
        open(f"{work}/sq.done", "w").close()
    if not os.path.exists(f"{work}/pl.done"):
        stream_encode((ref[i] for i in range(n)), W, H, pl_mkv)
        open(f"{work}/pl.done", "w").close()
    if not os.path.exists(f"{work}/unsq.done"):
        out = mm(f"{work}/unsq.u8", (n, H, W, 3), "w+")
        for i, d in enumerate(stream_decode(sq_mkv, SQ_W, SQ_H)):
            out[i] = np.clip(unsquish(d.astype(np.float32) / 255) * 255 + 0.5, 0, 255).astype(np.uint8)
        out.flush(); del out
        plain = mm(f"{work}/plain.u8", (n, H, W, 3), "w+")
        for i, d in enumerate(stream_decode(pl_mkv, W, H)):
            plain[i] = d
        plain.flush(); del plain
        open(f"{work}/unsq.done", "w").close()
        json.dump({"sq_kbps": os.path.getsize(sq_mkv) * 8 / (n / FPS) / 1000,
                   "plain_kbps": os.path.getsize(pl_mkv) * 8 / (n / FPS) / 1000},
                  open(f"{work}/bitrate.json", "w"), indent=1)
    print("prep done", flush=True)

def train(work, steps):
    torch.manual_seed(0); torch.set_num_threads(2)
    ref = mm(os.environ["REF_RGB"], (906, H, W, 3)); unsq = mm(f"{work}/unsq.u8", (906, H, W, 3))
    ck = f"{work}/restorer.pt"
    net = Restorer(); params = sum(p.numel() for p in net.parameters())
    opt = torch.optim.Adam(net.parameters(), lr=2e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, 1500)
    start = 0
    if os.path.exists(ck):
        s = torch.load(ck); net.load_state_dict(s["net"]); opt.load_state_dict(s["opt"]); sched.load_state_dict(s["sched"]); start = s["step"]
    rng = np.random.default_rng(start)
    crop, bs = 64, 16
    t0 = time.time()
    for step in range(start, start + steps):
        fi = rng.choice(TRAIN, bs)
        xs, ys = [], []
        for f in fi:
            y0, x0 = rng.integers(0, H - crop), rng.integers(0, W - crop)
            xs.append(unsq[f, y0:y0 + crop, x0:x0 + crop]); ys.append(ref[f, y0:y0 + crop, x0:x0 + crop])
        x = torch.from_numpy(np.stack(xs).astype(np.float32) / 255).permute(0, 3, 1, 2)
        y = torch.from_numpy(np.stack(ys).astype(np.float32) / 255).permute(0, 3, 1, 2)
        out = net(x)
        loss = F.l1_loss(out, y) + 2.0 * F.l1_loss(sobel(out.mean(1, keepdim=True)), sobel(y.mean(1, keepdim=True)))
        opt.zero_grad(); loss.backward(); opt.step(); sched.step()
        if (step + 1) % 100 == 0:
            print(f"step {step+1} loss {loss.item():.4f} {time.time()-t0:.0f}s", flush=True)
    torch.save({"net": net.state_dict(), "opt": opt.state_dict(), "sched": sched.state_dict(), "step": start + steps}, ck)
    json.dump({"params": params, "steps_done": start + steps}, open(f"{work}/train_info.json", "w"))
    print("saved", start + steps, "steps; params", params, flush=True)

def windows(work):
    mot = np.load(f"{work}/motion.npy")
    rng = np.random.default_rng(0)
    starts = sorted(int(s) for s in rng.integers(TEST_RANGE[0], TEST_RANGE[1] - WIN + 1, 4))
    scores = [float(mot[s:s + WIN].mean()) for s in starts]
    return starts, scores

def luma(x):  # (...,H,W,3) float [0,1] -> (...,H,W)
    return x @ np.array([0.299, 0.587, 0.114], np.float32)

OUTER = np.ones((H, W), bool)
OUTER[int(H * (0.5 - CENTER_C)):int(H * (0.5 + CENTER_C)), int(W * (0.5 - CENTER_C)):int(W * (0.5 + CENTER_C))] = False

def metric(pred, gt):  # luma (N,H,W) in [0,1]
    res = {}
    res["psnr"] = float(np.mean([10 * np.log10(1 / max(np.mean((p - g) ** 2), 1e-10)) for p, g in zip(pred, gt)]))
    res["ssim"] = float(np.mean([ssim_fn(p, g, data_range=1.0) for p, g in zip(pred, gt)]))
    res["outer_psnr"] = float(np.mean([10 * np.log10(1 / max(np.mean((p[OUTER] - g[OUTER]) ** 2), 1e-10)) for p, g in zip(pred, gt)]))
    sh = []
    for p, b in zip(pred, gt):
        a = sobel(torch.from_numpy(p)[None, None].float())[0, 0].numpy()
        g = sobel(torch.from_numpy(b)[None, None].float())[0, 0].numpy()
        m = OUTER & (g > np.percentile(g[OUTER], 90))
        sh.append(a[m].mean() / g[m].mean())
    res["outer_edge_sharpness"] = float(np.mean(sh))
    return {k: round(v, 4) for k, v in res.items()}

def run_eval(work, idx):
    torch.set_num_threads(2)
    ref = mm(os.environ["REF_RGB"], (906, H, W, 3))
    unsq = mm(f"{work}/unsq.u8", (906, H, W, 3)); plain = mm(f"{work}/plain.u8", (906, H, W, 3))
    starts, scores = windows(work)
    s = starts[idx]; idxs = range(s, s + WIN)
    gt = luma(ref[s:s + WIN].astype(np.float32) / 255)
    sq = unsq[s:s + WIN].astype(np.float32) / 255
    net = Restorer(); net.load_state_dict(torch.load(f"{work}/restorer.pt")["net"]); net.eval()
    with torch.no_grad():
        rest = np.concatenate([net(torch.from_numpy(sq[i:i + 8]).permute(0, 3, 1, 2)).permute(0, 2, 3, 1).numpy()
                               for i in range(0, WIN, 8)])
    arms = {
        "plain_1500k": lambda: luma(plain[s:s + WIN].astype(np.float32) / 255),
        "sq_bilinear": lambda: luma(sq),
        "sq_unsharp1.0": lambda: luma(np.clip(sq + (sq - np.stack([cv2.GaussianBlur(f, (0, 0), 1.5) for f in sq])), 0, 1)),
        "sq_ternary10k": lambda: luma(np.clip(rest, 0, 1)),
    }
    out = {"window_start_frame": s, "start_s": round(s / FPS, 2), "motion": round(scores[idx], 3), "arms": {}}
    for k, fn in arms.items():
        out["arms"][k] = metric(fn(), gt)
    json.dump(out, open(f"{work}/eval_{idx}.json", "w"), indent=1)
    print(json.dumps(out, indent=1), flush=True)

def report(work):
    starts, scores = windows(work)
    rows = [json.load(open(f"{work}/eval_{i}.json")) for i in range(4) if os.path.exists(f"{work}/eval_{i}.json")]
    busiest = int(np.argmax(scores))
    json.dump({"starts": starts, "motion": scores, "busiest_index": busiest, "windows": rows,
               "params": json.load(open(f"{work}/train_info.json"))["params"],
               "bitrate": json.load(open(f"{work}/bitrate.json"))}, open(f"{work}/report.json", "w"), indent=1)
    print(json.dumps({"starts": starts, "motion": scores, "busiest": busiest}, indent=1))

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "prep": prep(sys.argv[2], sys.argv[3])
    elif cmd == "train": train(sys.argv[2], int(sys.argv[3]))
    elif cmd == "eval": run_eval(sys.argv[2], int(sys.argv[3]))
    elif cmd == "report": report(sys.argv[2])
