"""Human comparison: plain h264_nvenc vs squish 56% + CNN + unsharp 50, at 1500, 3000 and 6000 kbit/s.

  python compare_demo.py TRAIN_WORK GAME.mp4 OUT_DIR [STEPS=3000]

TRAIN_WORK: a cnn_sweep work folder. Each clip folder holds ref.mkv and src_squish_1920x1080_1440x810.mkv.
One CNN is trained per bitrate on those clips only, so GAME is never seen by the restorer.
Needs CUDA torch, h264_nvenc, and an ffmpeg with libvmaf in $VMAF_FFMPEG.
Writes OUT_DIR: plain_h264_<kbps>.mp4 and squish_cnn_usm50_<kbps>.mp4 as separate files (no side-by-side merge), results.json.
Not included: an ROI arm. NVENC ignores addroi, so ROI on NVENC needs the R5 QP-delta map first.
"""
import glob, json, os, subprocess, sys
import numpy as np, torch, torch.nn as nn, torch.nn.functional as F
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stack"))
import stack_bench as sb

train_work, game, out = sys.argv[1], sys.argv[2], sys.argv[3]
steps = int(sys.argv[4]) if len(sys.argv) > 4 else 3000
W, H, SQ, CROP, RATES = 1920, 1080, (1440, 810), 192, [1500, 3000, 6000]
dev = "cuda" if torch.cuda.is_available() else "cpu"
os.makedirs(out, exist_ok=True)
os.environ.setdefault("CODEC", "h264_nvenc")  # set CODEC=x264 for the low rates NVENC cannot reach
sq = sb.Squish(W, H, *SQ)
here = os.path.dirname(os.path.abspath(__file__))
train_dirs = [os.path.dirname(p) for p in sorted(glob.glob(os.path.join(train_work, "*", "ref.mkv")))]
assert train_dirs, "no training clips in TRAIN_WORK"

GK = torch.exp(-(torch.arange(-3, 4, device=dev).float() ** 2) / 2.0); GK = (GK[:, None] * GK[None]) / (GK.sum() ** 2)
def usm(x, a):
    b = F.conv2d(F.pad(x, (3, 3, 3, 3), mode="reflect"), GK.expand(3, 1, 7, 7).contiguous(), groups=3)
    return (x + a * (x - b)).clamp(0, 1)

class Net(nn.Module):
    def __init__(s, c=24):
        super().__init__()
        s.b = nn.Sequential(nn.Conv2d(5, c, 3, padding=1), nn.ReLU(), nn.Conv2d(c, c, 3, padding=1), nn.ReLU(),
                            nn.Conv2d(c, c, 3, padding=1), nn.ReLU(), nn.Conv2d(c, 3, 3, padding=1))
    def forward(s, x, p): return x + s.b(torch.cat([x, p.expand(len(x), -1, -1, -1)], 1))

def pos(y0, x0, h, w):
    ys, xs = torch.meshgrid(torch.arange(y0, y0 + h, device=dev) / H, torch.arange(x0, x0 + w, device=dev) / W, indexing="ij")
    return torch.stack([xs, ys])[None]
P_FULL = pos(0, 0, H, W)

def encode(src, path, kbps):
    sb.encode(src, path, kbps)
    return path

def train_net(kbps):
    rng = np.random.default_rng(0); A, G, P = [], [], []
    for d in train_dirs:
        ref = os.path.join(d, "ref.mkv"); src = os.path.join(d, "src_squish_1920x1080_1440x810.mkv")
        tmp = encode(src, os.path.join(out, "_tmp_train.mkv"), kbps)
        rest = [sq.inv(f) for f in sb.frames(tmp, *SQ)]; os.remove(tmp)
        for i, (r, g) in enumerate(zip(rest, sb.frames(ref, W, H))):
            if i < 60 or i % 10: continue
            for _ in range(6):
                y, x = int(rng.integers(0, H - CROP)), int(rng.integers(0, W - CROP))
                A.append(r[y:y + CROP, x:x + CROP]); G.append(g[y:y + CROP, x:x + CROP]); P.append((y, x))
    A = torch.tensor(np.stack(A), device=dev).permute(0, 3, 1, 2); G = torch.tensor(np.stack(G), device=dev).permute(0, 3, 1, 2)
    Pp = torch.cat([pos(y, x, CROP, CROP) for y, x in P])
    net = Net().to(dev); opt = torch.optim.Adam(net.parameters(), 2e-3)
    sch = torch.optim.lr_scheduler.CosineAnnealingLR(opt, steps)
    for _ in range(steps):
        i = torch.randint(0, len(A), (16,), device=dev)
        loss = (net(A[i].float() / 255, Pp[i]) - G[i].float() / 255).abs().mean()
        opt.zero_grad(); loss.backward(); opt.step(); sch.step()
    return net.eval()

def cnn_restore(net, img):  # restored RGB uint8 frame -> CNN then unsharp 50
    x = torch.tensor(img, device=dev).permute(2, 0, 1)[None].float() / 255
    with torch.no_grad(): y = usm(net(x, P_FULL).clamp(0, 1), 0.5)
    return (y[0] * 255).round().byte().permute(1, 2, 0).cpu().numpy()

def write_lossless(frames, path):  # intermediate only: lossless so the one real encode comes later
    p = subprocess.Popen([sb.FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "60",
                          "-i", "-", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", "-an", path], stdin=subprocess.PIPE)
    for f in frames: p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close(); assert p.wait() == 0

def write_mp4(frames, path):
    p = subprocess.Popen([sb.FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "60",
                          "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "12", "-pix_fmt", "yuv420p", "-an", path],
                         stdin=subprocess.PIPE)
    for f in frames: p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close(); assert p.wait() == 0

def kbps_scored(path):  # bitrate over frames 60 onward, same window as the R1 runner
    b = sb.packets(path)[60:]
    return round(float(b.sum()) * 8 / (len(b) / 60) / 1000, 1)

# game source: lossless 1080p60 reference and its squish form
ref = os.path.join(out, "_ref_game.mkv")
subprocess.run([sys.executable, os.path.join(here, "..", "stack", "stack_bench.py"), "ref", game, ref, str(W), str(H)], check=True)
sqsrc = os.path.join(out, "_src_squish_game.mkv")
w = sb.lossless_writer(*SQ, sqsrc)
for f in sb.frames(ref, W, H): w.stdin.write(sq.fwd(f).tobytes())
w.stdin.close(); assert w.wait() == 0

results = {}
for kbps in RATES:
    net = train_net(kbps)
    plain = encode(ref, os.path.join(out, f"plain_h264_{kbps}.mp4"), kbps)
    ps, cs, v = sb.score(plain, ref, W, H, W, H, lambda f: f, os.path.join(out, f"_p_{kbps}.vmaf.json"))
    results[f"plain/{kbps}"] = {"kbps_scored": kbps_scored(plain), "psnr": round(float(ps[60:].mean()), 3),
                                "c_psnr": round(float(cs[60:].mean()), 3), "vmaf": round(float(v[60:].mean()), 2)}
    sqmkv = encode(sqsrc, os.path.join(out, f"_sq_{kbps}.mkv"), kbps)
    post = lambda f, net=net: cnn_restore(net, sq.inv(f))
    ps, cs, v = sb.score(sqmkv, ref, W, H, *SQ, post, os.path.join(out, f"_s_{kbps}.vmaf.json"))
    restored = os.path.join(out, f"_restored_{kbps}.mkv")
    write_lossless((post(f) for f in sb.frames(sqmkv, *SQ)), restored)
    viewer_mkv = os.path.join(out, f"squish_cnn_usm50_{kbps}.mkv")
    sb.encode(restored, viewer_mkv, kbps)
    viewer = os.path.join(out, f"squish_cnn_usm50_{kbps}.mp4")
    subprocess.run([sb.FF, "-v", "error", "-y", "-i", viewer_mkv, "-c", "copy", "-movflags", "+faststart", viewer], check=True)
    os.remove(restored); os.remove(viewer_mkv)
    results[f"squish56_cnn_usm50/{kbps}"] = {"kbps_scored": kbps_scored(sqmkv), "psnr": round(float(ps[60:].mean()), 3),
                                              "c_psnr": round(float(cs[60:].mean()), 3), "vmaf": round(float(v[60:].mean()), 2)}
    os.remove(sqmkv)
    json.dump(results, open(os.path.join(out, "results.json"), "w"), indent=1)
    print(kbps, results, flush=True)

for f in [ref, sqsrc]: os.remove(f)
open(os.path.join(out, "README.txt"), "w").write(
    "plain_h264_<kbps>.mp4: plain encode at full 1080p. squish_cnn_usm50_<kbps>.mp4: squish 56% pixels, bilinear unsquish, CNN, unsharp 50.\n"
    "Separate files on purpose: a side-by-side merge would re-compress both.\n"
    "Each bitrate is a rate-controlled CBR target; results.json has the bitrate actually produced.\n"
    "Both viewer files are one constant-bitrate encode at the target rate. The squish viewer encodes the restored picture, so it has one extra encode compared with the scored stream.\n"
    "The CNN was trained on other clips only, not on this game.\n"
    "No ROI arm: NVENC ignores addroi. The ROI arm needs the R5 QP-delta map.\n")
print("done")
