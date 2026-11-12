"""Minimap-only restorer vs full-frame restorer, same real-gameplay split as real_restore.py (#32 / #20).

Box (approved crop): x 10-197, y 340-514 in the 960x540 frame (188x175 px). Compass band and
facility bar are excluded. Metrics are computed inside the box only.

  minimap_restore.py train WORK STEPS     (trains on box crops from TRAIN frames only; resumable)
  minimap_restore.py eval  WORK IDX       (window IDX of real_restore's four windows)
  minimap_restore.py report WORK
"""
import json, os, sys
import numpy as np, cv2, torch, torch.nn.functional as F
from skimage.metrics import structural_similarity as ssim_fn
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import real_restore as rr

Y0, Y1, X0, X1 = 340, 515, 10, 198
BH, BW = Y1 - Y0, X1 - X0
N = 906

TOTAL = int(os.environ.get("MM_TOTAL", "1500"))   # cosine schedule length
CKPT = os.environ.get("MM_CKPT", "mm_restorer.pt")

def lr_at(step):
    return 2e-3 * 0.5 * (1 + np.cos(np.pi * min(step, TOTAL) / TOTAL))

def train(work, steps):
    torch.manual_seed(0); torch.set_num_threads(2)
    ref = rr.mm(os.environ["REF_RGB"], (N, rr.H, rr.W, 3)); unsq = rr.mm(f"{work}/unsq.u8", (N, rr.H, rr.W, 3))
    ck = f"{work}/{CKPT}"
    net = rr.Restorer(); params = sum(p.numel() for p in net.parameters())
    opt = torch.optim.Adam(net.parameters(), lr=2e-3)
    start = 0
    if os.path.exists(ck):
        s = torch.load(ck, weights_only=False); net.load_state_dict(s["net"]); opt.load_state_dict(s["opt"]); start = s["step"]
    rng = np.random.default_rng(start)
    crop, bs = 64, 16
    for step in range(start, start + steps):
        for g in opt.param_groups: g["lr"] = float(lr_at(step))
        fi = rng.choice(rr.TRAIN, bs)
        xs, ys = [], []
        for f in fi:
            y0, x0 = rng.integers(0, BH - crop + 1), rng.integers(0, BW - crop + 1)
            ys0, xs0 = Y0 + y0, X0 + x0
            xs.append(unsq[f, ys0:ys0 + crop, xs0:xs0 + crop]); ys.append(ref[f, ys0:ys0 + crop, xs0:xs0 + crop])
        x = torch.from_numpy(np.stack(xs).astype(np.float32) / 255).permute(0, 3, 1, 2)
        y = torch.from_numpy(np.stack(ys).astype(np.float32) / 255).permute(0, 3, 1, 2)
        out = net(x)
        loss = F.l1_loss(out, y) + 2.0 * F.l1_loss(rr.sobel(out.mean(1, keepdim=True)), rr.sobel(y.mean(1, keepdim=True)))
        opt.zero_grad(); loss.backward(); opt.step()
        if (step + 1) % 500 == 0: print(f"step {step+1} loss {loss.item():.4f}", flush=True)
    torch.save({"net": net.state_dict(), "opt": opt.state_dict(), "step": start + steps}, ck)
    json.dump({"params": params, "steps_done": start + steps, "total": TOTAL, "ckpt": CKPT}, open(f"{work}/mm_train_info.json", "w"))
    print("saved", start + steps, "params", params, flush=True)

def box_metrics(pred, gt):  # luma (N,BH,BW) in [0,1]
    psnr = float(np.mean([10 * np.log10(1 / max(np.mean((p - g) ** 2), 1e-10)) for p, g in zip(pred, gt)]))
    ss = float(np.mean([ssim_fn(p, g, data_range=1.0) for p, g in zip(pred, gt)]))
    sh = []
    for p, g in zip(pred, gt):
        a = rr.sobel(torch.from_numpy(p)[None, None].float())[0, 0].numpy()
        b = rr.sobel(torch.from_numpy(g)[None, None].float())[0, 0].numpy()
        m = b > np.percentile(b, 90)
        sh.append(a[m].mean() / b[m].mean())
    return {"psnr": round(psnr, 4), "ssim": round(ss, 4), "edge_sharpness": round(float(np.mean(sh)), 4)}

def run_restorer(net, frames_f32):  # (N,H,W,3) float [0,1] -> (N,H,W,3)
    with torch.no_grad():
        return np.concatenate([net(torch.from_numpy(frames_f32[i:i + 8]).permute(0, 3, 1, 2)).permute(0, 2, 3, 1).numpy()
                               for i in range(0, len(frames_f32), 8)])

def run_eval(work, idx):
    torch.set_num_threads(2)
    ref = rr.mm(os.environ["REF_RGB"], (N, rr.H, rr.W, 3))
    unsq = rr.mm(f"{work}/unsq.u8", (N, rr.H, rr.W, 3)); plain = rr.mm(f"{work}/plain.u8", (N, rr.H, rr.W, 3))
    starts, scores = rr.windows(work)
    s = starts[idx]; e = s + rr.WIN
    gt_box = rr.luma(ref[s:e, Y0:Y1, X0:X1].astype(np.float32) / 255)
    sq = unsq[s:e].astype(np.float32) / 255
    full = rr.Restorer(); full.load_state_dict(torch.load(f"{work}/restorer.pt")["net"]); full.eval()
    mm = rr.Restorer(); mm.load_state_dict(torch.load(f"{work}/{CKPT}")["net"]); mm.eval()
    full_out = run_restorer(full, sq)[:, Y0:Y1, X0:X1]
    mm_out = run_restorer(mm, sq[:, Y0:Y1, X0:X1])
    box = lambda a: a[:, Y0:Y1, X0:X1]
    arms = {
        "plain_1500k": rr.luma(box(plain[s:e].astype(np.float32) / 255)),
        "sq_bilinear": rr.luma(box(sq)),
        "sq_unsharp1.0": rr.luma(np.clip(box(sq) + (box(sq) - np.stack([cv2.GaussianBlur(f, (0, 0), 1.5) for f in box(sq)])), 0, 1)),
        "sq_fullframe_ternary": rr.luma(np.clip(full_out, 0, 1)),
        "sq_minimap_ternary": rr.luma(np.clip(mm_out, 0, 1)),
    }
    out = {"start_s": round(s / rr.FPS, 2), "arms": {}}
    for k, v in arms.items():
        out["arms"][k] = box_metrics(v, gt_box)
    json.dump(out, open(f"{work}/mm_eval_{idx}{CKPT.replace('.pt','')}.json" if CKPT!="mm_restorer.pt" else f"{work}/mm_eval_{idx}.json", "w"), indent=1)
    print(json.dumps(out, indent=1), flush=True)

def report(work):
    rows = [json.load(open(f"{work}/mm_eval_{i}.json")) for i in range(4) if os.path.exists(f"{work}/mm_eval_{i}.json")]
    info = json.load(open(f"{work}/mm_train_info.json"))
    json.dump({"box": [Y0, Y1, X0, X1], "params": info["params"], "windows": rows},
              open(f"{work}/mm_report.json", "w"), indent=1)
    print(json.dumps({"params": info["params"], "windows": len(rows)}))

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "train": train(sys.argv[2], int(sys.argv[3]))
    elif cmd == "eval": run_eval(sys.argv[2], int(sys.argv[3]))
    elif cmd == "report": report(sys.argv[2])


AMOUNTS = (0.4, 0.6, 0.8, 1.0)

def unsharp(x, amt):
    return np.clip(x + amt * (x - np.stack([cv2.GaussianBlur(f, (0, 0), 1.5) for f in x])), 0, 1)

def run_sweep(work, idx):
    """Minimap model output, then unsharp (sigma 1.5) at several amounts, same box metrics."""
    torch.set_num_threads(2)
    ref = rr.mm(os.environ["REF_RGB"], (N, rr.H, rr.W, 3)); unsq = rr.mm(f"{work}/unsq.u8", (N, rr.H, rr.W, 3))
    starts, _ = rr.windows(work)
    s = starts[idx]; e = s + rr.WIN
    gt_box = rr.luma(ref[s:e, Y0:Y1, X0:X1].astype(np.float32) / 255)
    sq_box = unsq[s:e, Y0:Y1, X0:X1].astype(np.float32) / 255
    mm = rr.Restorer(); mm.load_state_dict(torch.load(f"{work}/{CKPT}")["net"]); mm.eval()
    base = np.clip(run_restorer(mm, sq_box), 0, 1)
    out = {"start_s": round(s / rr.FPS, 2), "arms": {"minimap_ternary": box_metrics(rr.luma(base), gt_box)}}
    for a in AMOUNTS:
        out["arms"][f"minimap_ternary+unsharp{a}"] = box_metrics(rr.luma(unsharp(base, a)), gt_box)
    json.dump(out, open(f"{work}/mm_sweep_{idx}.json", "w"), indent=1)
    print(json.dumps(out, indent=1), flush=True)

if __name__ == "__main__" and sys.argv[1] == "sweep":
    run_sweep(sys.argv[2], int(sys.argv[3]))
