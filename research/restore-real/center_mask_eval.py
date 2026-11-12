"""Center-masked restoration: center pixels from bilinear unsquish, outside from the model (6000 kbit/s)."""
import json, os, sys
import numpy as np, torch
from skimage.metrics import structural_similarity as ssim_fn
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import real_restore as rr
import eval6000_models as ev
from center_metrics import Y0, Y1, X0, X1

N = 906
MASK = np.zeros((rr.H, rr.W), bool); MASK[Y0:Y1, X0:X1] = True

def full_metrics(pred, gt):
    pred, gt = rr.luma(pred), rr.luma(gt)
    psnr = float(np.mean([10 * np.log10(1 / max(np.mean((a - b) ** 2), 1e-10)) for a, b in zip(pred, gt)]))
    ss = float(np.mean([ssim_fn(a, b, data_range=1.0) for a, b in zip(pred, gt)]))
    return {"psnr": round(psnr, 3), "ssim": round(ss, 4)}

def compose(model_out, bilinear):  # model outside the center box, bilinear (exact 1:1 center) inside
    out = model_out.copy()
    out[:, MASK] = bilinear[:, MASK]
    return out

if __name__ == "__main__":
    ref_path, work, outjson = sys.argv[1], sys.argv[2], sys.argv[3]
    torch.set_num_threads(2)
    ref = rr.mm(ref_path, (N, rr.H, rr.W, 3)); unsq = rr.mm(f"{work}/unsq6000.u8", (N, rr.H, rr.W, 3))
    nets = {"float6000": ev.load(work, "f6000.pt", "float"), "ternary6000": ev.load(work, "t6000.pt", "ternary")}
    acc = {}
    for s in rr.windows(work)[0]:
        e = s + rr.WIN
        gt = ref[s:e].astype(np.float32) / 255
        sq = unsq[s:e].astype(np.float32) / 255
        def add(k, v):
            acc.setdefault(k, []).append(full_metrics(v, gt))
        add("sq6000_bilinear", sq)
        for k, net in nets.items():
            r = ev.run(net, sq)
            add(k, r)
            add(k + "_center_masked", compose(r, sq))
            ru = ev.unsharp(r, 0.25)
            add(k + "_unsharp0.25", ru)
            add(k + "_unsharp0.25_center_masked", compose(ru, sq))
            del r, ru
    res = {k: {q: round(float(np.mean([d[q] for d in v])), 4) for q in ["psnr", "ssim"]} for k, v in acc.items()}
    json.dump({"arms": res}, open(outjson, "w"), indent=1)
    for k, v in res.items(): print(f"{k:42s} psnr {v['psnr']:.3f} ssim {v['ssim']:.4f}")
