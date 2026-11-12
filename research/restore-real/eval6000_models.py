"""Four-window eval on the 6000 kbit/s squish arm for several restorers (full frame, luma).
  eval6000_models.py REF_RGB WORK OUTJSON MODEL [MODEL ...]
  MODEL = name=ckpt_file:kind[:unsharp]   kind in {ternary, float}; ckpt in WORK
"""
import json, os, sys
import numpy as np, cv2, torch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import real_restore as rr
import train6000 as t6

N = 906

def load(work, ckpt, kind):
    net = t6.make(kind)
    net.load_state_dict(torch.load(f"{work}/{ckpt}", weights_only=False)["net"]); net.eval()
    return net

def run(net, frames):
    with torch.no_grad():
        return np.clip(np.concatenate([net(torch.from_numpy(frames[i:i + 8]).permute(0, 3, 1, 2)).permute(0, 2, 3, 1).numpy()
                                       for i in range(0, len(frames), 8)]), 0, 1)

def unsharp(x, a):
    return np.clip(x + a * (x - np.stack([cv2.GaussianBlur(f, (0, 0), 1.5) for f in x])), 0, 1)

if __name__ == "__main__":
    ref_path, work, outjson = sys.argv[1], sys.argv[2], sys.argv[3]
    specs = sys.argv[4:]
    torch.set_num_threads(2)
    ref = rr.mm(ref_path, (N, rr.H, rr.W, 3))
    unsq = rr.mm(f"{work}/unsq6000.u8", (N, rr.H, rr.W, 3)); plain = rr.mm(f"{work}/plain6000.u8", (N, rr.H, rr.W, 3))
    models = []
    for sp in specs:
        name, rest = sp.split("=", 1)
        parts = rest.split(":")
        models.append((name, parts[0], parts[1], float(parts[2]) if len(parts) > 2 else 0.0))
    nets = {m[1]: load(work, m[1], m[2]) for m in models}
    starts, _ = rr.windows(work)
    out = []
    for s in starts:
        e = s + rr.WIN
        gt = rr.luma(ref[s:e].astype(np.float32) / 255)
        sq = unsq[s:e].astype(np.float32) / 255
        arms = {"plain_6000k": rr.luma(plain[s:e].astype(np.float32) / 255), "sq6000_bilinear": rr.luma(sq)}
        for name, ckpt, kind, amt in models:
            r = run(nets[ckpt], sq)
            r = unsharp(r, amt) if amt else r
            arms[name] = rr.luma(r)
        row = {"start_s": round(s / rr.FPS, 2), "arms": {k: rr.metric(v, gt) for k, v in arms.items()}}
        out.append(row); print(json.dumps(row), flush=True)
    json.dump({"windows": out}, open(outjson, "w"), indent=1)
    keys = list(out[0]["arms"].keys())
    for k in keys:
        m = {q: np.mean([w["arms"][k][q] for w in out]) for q in ["psnr", "ssim", "outer_edge_sharpness"]}
        print(f"{k:34s} psnr {m['psnr']:.3f} ssim {m['ssim']:.4f} edge {m['outer_edge_sharpness']:.3f}")
