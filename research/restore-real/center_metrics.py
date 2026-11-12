"""Center-box metrics (sharp 1:1 center of Foveated Squish) for the 6000 kbit/s arms, four windows."""
import json, os, sys
import numpy as np, cv2, torch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import real_restore as rr
import eval6000_models as ev

N = 906
Y0, Y1 = int(rr.H * (0.5 - rr.CENTER_C)), int(rr.H * (0.5 + rr.CENTER_C))
X0, X1 = int(rr.W * (0.5 - rr.CENTER_C)), int(rr.W * (0.5 + rr.CENTER_C))

def center_metrics(pred, gt):
    p, g = pred[:, Y0:Y1, X0:X1], gt[:, Y0:Y1, X0:X1]
    from skimage.metrics import structural_similarity as ssim_fn
    psnr = float(np.mean([10 * np.log10(1 / max(np.mean((a - b) ** 2), 1e-10)) for a, b in zip(p, g)]))
    ss = float(np.mean([ssim_fn(a, b, data_range=1.0) for a, b in zip(p, g)]))
    sh = []
    for a, b in zip(p, g):
        ga = rr.sobel(torch.from_numpy(a)[None, None].float())[0, 0].numpy()
        gb = rr.sobel(torch.from_numpy(b)[None, None].float())[0, 0].numpy()
        m = gb > np.percentile(gb, 90)
        sh.append(ga[m].mean() / gb[m].mean())
    return {"psnr": round(psnr, 3), "ssim": round(ss, 4), "edge": round(float(np.mean(sh)), 3)}

if __name__ == "__main__":
    ref_path, work, outjson = sys.argv[1], sys.argv[2], sys.argv[3]
    torch.set_num_threads(2)
    ref = rr.mm(ref_path, (N, rr.H, rr.W, 3))
    unsq = rr.mm(f"{work}/unsq6000.u8", (N, rr.H, rr.W, 3)); plain = rr.mm(f"{work}/plain6000.u8", (N, rr.H, rr.W, 3))
    nets = {"float6000": ev.load(work, "f6000.pt", "float"), "ternary6000": ev.load(work, "t6000.pt", "ternary"),
            "ternary1500": ev.load(work, "restorer.pt", "ternary")}
    acc = {}
    for s in rr.windows(work)[0]:
        e = s + rr.WIN
        gt = rr.luma(ref[s:e].astype(np.float32) / 255)
        sq = unsq[s:e].astype(np.float32) / 255
        def add(k, v):
            acc.setdefault(k, []).append(center_metrics(v, gt))
        add("plain_6000k", rr.luma(plain[s:e].astype(np.float32) / 255))
        add("sq6000_bilinear", rr.luma(sq))
        for k, net in nets.items():
            r = ev.run(net, sq)
            add(k, rr.luma(r))
            add(k + "_unsharp0.25", rr.luma(ev.unsharp(r, 0.25)))
            del r
    res = {k: {q: round(float(np.mean([d[q] for d in v])), 4) for q in ["psnr", "ssim", "edge"]} for k, v in acc.items()}
    json.dump({"center_box": [Y0, Y1, X0, X1], "arms": res}, open(outjson, "w"), indent=1)
    for k, v in res.items(): print(f"{k:28s} center psnr {v['psnr']:.3f} ssim {v['ssim']:.4f} edge {v['edge']:.3f}")
