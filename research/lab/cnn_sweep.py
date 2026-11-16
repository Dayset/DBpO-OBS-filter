"""Full sweep: which restoration recipe wins, for which codec, bitrate and clip (task E5b).

  python cnn_sweep.py WORK_DIR [CODECS=h264_nvenc,hevc_nvenc,av1_nvenc,x264] [RATES=6000,3000] [STEPS=3000] [EVAL_SECS=4]
  python cnn_sweep.py WORK_DIR summary          (prints the tables, run any time, also while the sweep runs)

Leave-one-clip-out: every clip in WORK_DIR (folders with ref.mkv + src_squish_1920x1080_1440x810.mkv, made by run_lab.py) is the
held-out test clip once; the CNN trains on all the others. Per codec, rate, arm (squish56 = squish + bicubic unsquish,
scale810 = plain downscale + bicubic upscale) it scores these recipes on the held-out clip (VMAF, p5, PSNR, centre PSNR):
  bicubic | usm25, usm50 (unsharp after bicubic) | cnn | cnn_masked (CNN only outside the 1:1 centre 30%, bicubic inside)
  cnn+usm25, cnn+usm50 (unsharp after CNN) | usm50>cnn (unsharp first, then a CNN trained on unsharped input)
Resumable: finished (clip, codec, rate, arm) cells are skipped; results go to WORK_DIR/results/sweep.jsonl one line per recipe.
Close the game first (GPU). Needs the torch venv (see cnn_lab.py).
"""
import glob, itertools, json, os, random, subprocess, sys
import cv2, numpy as np, torch, torch.nn as nn, torch.nn.functional as F
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stack"))
os.environ["UNSQ_CUBIC"] = "1"
import stack_bench as sb

work = sys.argv[1]
OUT = os.path.join(work, "results", "sweep.jsonl")
W, H, SQ, CROP = 1920, 1080, (1440, 810), 192
dev = "cuda" if torch.cuda.is_available() else "cpu"

def rows():
    return [json.loads(l) for l in open(OUT)] if os.path.exists(OUT) else []

def summary():
    import collections, statistics as st
    R = rows(); g = collections.defaultdict(list)
    for r in R: g[(r["codec"], r["kbps"], r["arm"], r["recipe"])].append(r)
    base = {(r["clip"], r["codec"], r["kbps"], r["arm"]): r for r in R if r["recipe"] == "bicubic"}
    print("codec rate arm recipe | n | VMAF mean | dVMAF vs bicubic (mean, wins/n) | p5 | centre dB | whole PSNR")
    for k in sorted(g):
        v = g[k]; d = [r["vmaf"] - base[(r["clip"],) + k[:3]]["vmaf"] for r in v if (r["clip"],) + k[:3] in base]
        print(*k, "|", len(v), "|", round(st.mean(r["vmaf"] for r in v), 2), "|", round(st.mean(d), 2) if d else "-", f"{sum(x > 0 for x in d)}/{len(d)}",
              "|", round(st.mean(r["vmaf_p5"] for r in v), 2), "|", round(st.mean(r["c_psnr"] for r in v), 2), "|", round(st.mean(r["psnr"] for r in v), 2))
    print("\nbest recipe per codec/rate (whole-frame VMAF, mean over clips), both arms, plus squish minus scale810 for the same recipe:")
    for c, kb in sorted({(k[0], k[1]) for k in g}):
        best = max(((st.mean(r["vmaf"] for r in v), k[2], k[3]) for k, v in g.items() if k[:2] == (c, kb)), default=None)
        print(c, kb, "best:", best)
if len(sys.argv) > 2 and sys.argv[2] == "summary": summary(); sys.exit()

codecs = (sys.argv[2] if len(sys.argv) > 2 else "h264_nvenc,hevc_nvenc,av1_nvenc,x264").split(",")
rates = [int(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "6000,3000").split(",")]
steps = int(sys.argv[4]) if len(sys.argv) > 4 else 3000
eval_secs = int(sys.argv[5]) if len(sys.argv) > 5 else 4
dirs = [os.path.dirname(p) for p in sorted(glob.glob(os.path.join(work, "*", "ref.mkv")))]
assert len(dirs) >= 2, "need at least two clips"
name = os.path.basename

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

def restored(d, arm, codec, kbps):
    if arm == "squish56": src, vf, post = os.path.join(d, "src_squish_1920x1080_1440x810.mkv"), None, sb.Squish(W, H, *SQ).inv
    else: src, vf, post = os.path.join(d, "ref.mkv"), "scale=1440:810:flags=bicubic", lambda f: cv2.resize(f, (W, H), interpolation=cv2.INTER_CUBIC)
    mkv = os.path.join(d, f"sw_{arm}_{codec}_{kbps}.mkv")
    if not os.path.exists(mkv):
        os.environ["CODEC"] = codec; sb.encode(src, mkv, kbps, vf)
    return (post(f) for f in sb.frames(mkv, *SQ))

def crops(d, arm, codec, kbps, every=10, per=6):
    out = []
    for i, (a, g) in enumerate(zip(restored(d, arm, codec, kbps), sb.frames(os.path.join(d, "ref.mkv"), W, H))):
        if i < 60 or i % every: continue
        for _ in range(per):
            y, x = random.randrange(H - CROP), random.randrange(W - CROP)
            out.append((a[y:y + CROP, x:x + CROP].copy(), g[y:y + CROP, x:x + CROP].copy(), y, x))
    return out

def train(A, G, P, pre):
    net = Net().to(dev); opt = torch.optim.Adam(net.parameters(), 2e-3); sch = torch.optim.lr_scheduler.CosineAnnealingLR(opt, steps)
    for _ in range(steps):
        i = torch.randint(0, len(A), (16,), device=dev)
        loss = (net(pre(A[i].float() / 255), P[i]) - G[i].float() / 255).abs().mean()
        opt.zero_grad(); loss.backward(); opt.step(); sch.step()
    return net

RECIPES = ["bicubic", "usm25", "usm50", "cnn", "cnn_masked", "cnn+usm25", "cnn+usm50", "usm50>cnn"]
y0, y1, x0, x1 = int(H * .35), int(H * .65), int(W * .35), int(W * .65)

def evaluate(d, arm, codec, kbps, net, net_u):
    n = 60 + eval_secs * 60; ref = os.path.join(d, "ref.mkv"); rev = os.path.join(d, "ref_eval.mkv")
    subprocess.run([sb.FF, "-v", "error", "-y", "-i", ref, "-frames:v", str(n), "-c", "copy", rev], check=True)
    files = {r: os.path.join(d, f"sw_{r.replace('>', '_').replace('+', '_')}.mkv") for r in RECIPES}
    wr = {r: sb.lossless_writer(W, H, f) for r, f in files.items()}; P = pos(0, 0, H, W)
    for f in itertools.islice(restored(d, arm, codec, kbps), n):
        with torch.no_grad():
            x = torch.tensor(f, device=dev).permute(2, 0, 1)[None].float() / 255; c = net(x, P).clamp(0, 1)
            m = x.clone(); m[..., y0:y1, x0:x1] = c[..., y0:y1, x0:x1]; m = x.clone(); m[..., :, :] = c; m[..., y0:y1, x0:x1] = x[..., y0:y1, x0:x1]
            out = {"bicubic": x, "usm25": usm(x, .25), "usm50": usm(x, .5), "cnn": c, "cnn_masked": m,
                   "cnn+usm25": usm(c, .25), "cnn+usm50": usm(c, .5), "usm50>cnn": net_u(usm(x, .5), P).clamp(0, 1)}
        for r, t in out.items(): wr[r].stdin.write((t[0] * 255).round().byte().permute(1, 2, 0).cpu().numpy().tobytes())
    res = {}
    for r in RECIPES:
        wr[r].stdin.close(); assert wr[r].wait() == 0
        ps, cs, v = sb.score(files[r], rev, W, H, W, H, lambda f: f, files[r] + ".vmaf.json"); k = slice(60, None)
        res[r] = {"psnr": round(float(ps[k].mean()), 3), "c_psnr": round(float(cs[k].mean()), 3), "vmaf": round(float(v[k].mean()), 2), "vmaf_p5": round(float(np.percentile(v[k], 5)), 2)}
        os.remove(files[r])
    os.remove(rev); return res

os.makedirs(os.path.dirname(OUT), exist_ok=True)
for codec, kbps in itertools.product(codecs, rates):
    for arm in ("squish56", "scale810"):
        todo = [d for d in dirs if not any(r["clip"] == name(d) and (r["codec"], r["kbps"], r["arm"]) == (codec, kbps, arm) for r in rows())]
        if not todo: continue
        print("==", codec, kbps, arm, "building crops", flush=True)
        data = [(i, c) for i, d in enumerate(dirs) for c in crops(d, arm, codec, kbps)]
        A = torch.tensor(np.stack([c[1][0] for c in data]), device=dev).permute(0, 3, 1, 2); G = torch.tensor(np.stack([c[1][1] for c in data]), device=dev).permute(0, 3, 1, 2)
        Pp = torch.cat([pos(c[1][2], c[1][3], CROP, CROP) for c in data]); who = np.array([c[0] for c in data])
        for d in todo:
            keep = torch.tensor(np.where(who != dirs.index(d))[0], device=dev)
            net = train(A[keep], G[keep], Pp[keep], lambda x: x); net_u = train(A[keep], G[keep], Pp[keep], lambda x: usm(x, .5))
            res = evaluate(d, arm, codec, kbps, net, net_u)
            with open(OUT, "a") as f:
                for r, m in res.items(): f.write(json.dumps({"clip": name(d), "codec": codec, "kbps": kbps, "arm": arm, "recipe": r, **m}) + "\n")
            print(name(d)[-12:], codec, kbps, arm, {r: m["vmaf"] for r, m in res.items()}, flush=True)
summary()
