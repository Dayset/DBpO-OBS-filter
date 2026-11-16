"""CNN restorer on top of bicubic unsquish (and on top of a plain bicubic upscale, the control), trained and tested on the PC.

  python cnn_lab.py WORK_DIR HOLDOUT [STEPS=4000] [KBPS=6000]     env: CODEC (default h264_nvenc)

WORK_DIR is run_lab.py's output (needs <clip>/ref.mkv and <clip>/src_squish_1920x1080_1440x810.mkv). HOLDOUT is part of one
clip folder name: that clip is only used for testing, every other clip trains (so close PlanetSide 2 first, the GPU is used).
Per arm (squish56 with bicubic unsquish, scale810 with bicubic upscale) it trains a 12k-parameter residual CNN on random
crops of the decoded 1080p-restored frames (inputs include x,y so it can learn the squish ratio), then scores on the held-out
clip, with VMAF and centre PSNR via stack_bench.score: bicubic alone vs bicubic + CNN. Result: WORK_DIR/results/cnn_<HOLDOUT>_<codec>.json
Needs: pip install torch (CUDA build: https://pytorch.org/get-started/locally/), plus what run_lab.py needs.
"""
import glob, json, os, random, sys
import cv2, numpy as np, torch, torch.nn as nn
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stack"))
os.environ.setdefault("UNSQ_CUBIC", "1"); os.environ.setdefault("CODEC", "h264_nvenc")
import stack_bench as sb

work, hold = sys.argv[1], sys.argv[2]
steps = int(sys.argv[3]) if len(sys.argv) > 3 else 4000
kbps = int(sys.argv[4]) if len(sys.argv) > 4 else 6000
W, H, SQ, CROP, dev = 1920, 1080, (1440, 810), 192, "cuda" if torch.cuda.is_available() else "cpu"
dirs = [os.path.dirname(p) for p in sorted(glob.glob(os.path.join(work, "*", "ref.mkv")))]
test = [d for d in dirs if hold in os.path.basename(d)]; train = [d for d in dirs if d not in test]
assert len(test) == 1 and train, f"need exactly one holdout match and other clips, got {test}"

def restored(d, arm):
    """Frames of clip d after encode, decode and the plain (bicubic) restoration back to 1080p."""
    if arm == "squish56": src, vf, post = os.path.join(d, "src_squish_1920x1080_1440x810.mkv"), None, sb.Squish(W, H, *SQ).inv
    else: src, vf, post = os.path.join(d, "ref.mkv"), "scale=1440:810:flags=bicubic", lambda f: cv2.resize(f, (W, H), interpolation=cv2.INTER_CUBIC)
    mkv = os.path.join(d, f"cnn_{arm}_{os.environ['CODEC']}_{kbps}.mkv")
    if not os.path.exists(mkv): sb.encode(src, mkv, kbps, vf)
    return (post(f) for f in sb.frames(mkv, *SQ))

def pos(y0, x0, h, w):
    ys, xs = torch.meshgrid(torch.arange(y0, y0 + h, device=dev) / H, torch.arange(x0, x0 + w, device=dev) / W, indexing="ij")
    return torch.stack([xs, ys])[None]

class Net(nn.Module):
    def __init__(s, c=24):
        super().__init__()
        s.b = nn.Sequential(nn.Conv2d(5, c, 3, padding=1), nn.ReLU(), nn.Conv2d(c, c, 3, padding=1), nn.ReLU(),
                            nn.Conv2d(c, c, 3, padding=1), nn.ReLU(), nn.Conv2d(c, 3, 3, padding=1))
    def forward(s, x, p): return x + s.b(torch.cat([x, p.expand(len(x), -1, -1, -1)], 1))

def crops(d, arm, every=10, per=6):
    out = []
    for i, (a, g) in enumerate(zip(restored(d, arm), sb.frames(os.path.join(d, "ref.mkv"), W, H))):
        if i < 60 or i % every: continue
        for _ in range(per):
            y, x = random.randrange(H - CROP), random.randrange(W - CROP)
            out.append((a[y:y + CROP, x:x + CROP].copy(), g[y:y + CROP, x:x + CROP].copy(), y, x))
    return out

def run(arm):
    data = [c for d in train for c in crops(d, arm)]
    A = torch.tensor(np.stack([c[0] for c in data]), device=dev).permute(0, 3, 1, 2); G = torch.tensor(np.stack([c[1] for c in data]), device=dev).permute(0, 3, 1, 2)
    P = torch.cat([pos(c[2], c[3], CROP, CROP) for c in data])
    net = Net().to(dev); opt = torch.optim.Adam(net.parameters(), 2e-3); sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, steps)
    print(arm, "params", sum(p.numel() for p in net.parameters()), "crops", len(data), flush=True)
    for s in range(steps):
        i = torch.randint(0, len(data), (16,), device=dev)
        loss = (net(A[i].float() / 255, P[i]) - G[i].float() / 255).abs().mean()
        opt.zero_grad(); loss.backward(); opt.step(); sched.step()
        if s % 500 == 0: print(arm, s, round(loss.item(), 5), flush=True)
    d = test[0]; ref = os.path.join(d, "ref.mkv"); res = {}
    for name in ("bicubic", "bicubic+cnn"):
        out = os.path.join(d, f"cnn_{arm}_{name}.mkv"); w = sb.lossless_writer(W, H, out)
        for f in restored(d, arm):
            if name == "bicubic": w.stdin.write(f.tobytes()); continue
            with torch.no_grad(): y = net(torch.tensor(f, device=dev).permute(2, 0, 1)[None].float() / 255, pos(0, 0, H, W))
            w.stdin.write((y[0].clamp(0, 1) * 255).round().byte().permute(1, 2, 0).cpu().numpy().tobytes())
        w.stdin.close(); assert w.wait() == 0
        ps, cs, v = sb.score(out, ref, W, H, W, H, lambda f: f, os.path.join(d, f"cnn_{arm}_{name}.vmaf.json")); k = slice(60, None)
        res[name] = {"psnr": round(float(ps[k].mean()), 3), "c_psnr": round(float(cs[k].mean()), 3), "vmaf": round(float(v[k].mean()), 2), "vmaf_p5": round(float(np.percentile(v[k], 5)), 2)}
        os.remove(out); print(arm, name, res[name], flush=True)
    torch.save(net.state_dict(), os.path.join(work, f"cnn_{arm}.pt"))
    return res

out = {a: run(a) for a in ("squish56", "scale810")}
p = os.path.join(work, "results", f"cnn_{hold}_{os.environ['CODEC']}.json"); json.dump(out, open(p, "w"), indent=1); print("done", p)
