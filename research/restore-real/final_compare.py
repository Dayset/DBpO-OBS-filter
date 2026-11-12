"""Final comparison at 1500 kbit/s (matched starvation, = 6000 kbit/s at 1080p): baseline plain stream vs
squish arms, float and ternary restorers, unsharp off/on, centre raw and centre-masked.
Per-window JSON so each call stays short:  final_compare.py REF WORK WINDOW_IDX
Aggregate with:  final_compare.py report WORK
"""
import json, os, sys
import numpy as np, torch
from skimage.metrics import structural_similarity as ssim_fn
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import real_restore as rr
import eval6000_models as ev
from center_metrics import Y0, Y1, X0, X1, center_metrics
from center_mask_eval import compose

N = 906

def full(pred, gt):
    p, g = rr.luma(pred), rr.luma(gt)
    psnr = float(np.mean([10 * np.log10(1 / max(np.mean((a - b) ** 2), 1e-10)) for a, b in zip(p, g)]))
    ss = float(np.mean([ssim_fn(a, b, data_range=1.0) for a, b in zip(p, g)]))
    return {"psnr": psnr, "ssim": ss}

def run_window(ref_path, work, idx):
    torch.set_num_threads(2)
    ref = rr.mm(ref_path, (N, rr.H, rr.W, 3))
    unsq = rr.mm(f"{work}/unsq.u8", (N, rr.H, rr.W, 3)); plain = rr.mm(f"{work}/plain.u8", (N, rr.H, rr.W, 3))
    s = rr.windows(work)[0][idx]; e = s + rr.WIN
    gt = ref[s:e].astype(np.float32) / 255
    sq = unsq[s:e].astype(np.float32) / 255
    res = {}
    def add(name, pred):
        f = full(pred, gt)
        c = center_metrics(rr.luma(pred), rr.luma(gt))
        res[name] = {"psnr": f["psnr"], "ssim": f["ssim"], "c_psnr": c["psnr"], "c_ssim": c["ssim"], "c_edge": c["edge"]}
    add("plain_1500k", plain[s:e].astype(np.float32) / 255)
    add("sq1500_bilinear", sq)
    for mname, ck, kind in [("ternary", "restorer.pt", "ternary"), ("float", "f1500.pt", "float")]:
        net = ev.load(work, ck, kind)
        r = ev.run(net, sq)
        for amt in (0.0, 0.25):
            ru = ev.unsharp(r, amt) if amt else r
            tag = f"{mname}" + (f"_unsharp{amt}" if amt else "")
            add(tag, ru)
            add(tag + "_centermasked", compose(ru, sq))
        del r, ru
    out = {"start_s": round(s / rr.FPS, 2), "arms": res}
    json.dump(out, open(f"{work}/final1500_{idx}.json", "w"), indent=1)
    print(json.dumps(out["arms"]["plain_1500k"]), flush=True)

def report(work):
    wins = [json.load(open(f"{work}/final1500_{i}.json")) for i in range(4)]
    keys = list(wins[0]["arms"].keys())
    base = {q: np.mean([w["arms"]["plain_1500k"][q] for w in wins]) for q in ["psnr", "ssim", "c_psnr", "c_ssim", "c_edge"]}
    rows = []
    for k in keys:
        m = {q: float(np.mean([w["arms"][k][q] for w in wins])) for q in ["psnr", "ssim", "c_psnr", "c_ssim", "c_edge"]}
        rows.append({"arm": k, **{q: round(v, 4) for q, v in m.items()},
                     "d_psnr_vs_plain": round(m["psnr"] - base["psnr"], 3), "d_cpsnr_vs_plain": round(m["c_psnr"] - base["c_psnr"], 3)})
    json.dump({"rows": rows}, open(f"{work}/final1500_report.json", "w"), indent=1)
    print(f"{'arm':34s} {'PSNR':>7s} {'dPSNR':>7s} {'SSIM':>7s} {'cPSNR':>7s} {'dcPSNR':>7s} {'cSSIM':>7s} {'cEdge':>6s}")
    for r in rows:
        print(f"{r['arm']:34s} {r['psnr']:7.3f} {r['d_psnr_vs_plain']:+7.3f} {r['ssim']:7.4f} {r['c_psnr']:7.3f} {r['d_cpsnr_vs_plain']:+7.3f} {r['c_ssim']:7.4f} {r['c_edge']:6.3f}")

if __name__ == "__main__":
    if sys.argv[1] == "report":
        report(sys.argv[2])
    else:
        run_window(sys.argv[1], sys.argv[2], int(sys.argv[3]))
