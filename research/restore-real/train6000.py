"""Train a 10k restorer on 6000 kbit/s squish inputs (target = reference), ternary or float.

  train6000.py ternary WORK STEPS   -> WORK/t6000.pt   (ternary, same as real_restore.Restorer)
  train6000.py float   WORK STEPS   -> WORK/f6000.pt   (float, same layout, plain Conv2d)
Input: WORK/unsq6000.u8 (6000 kbit/s squish, unsquished). Target: REF_RGB. Train frames as real_restore.TRAIN.
Resumable; cosine schedule over TOTAL steps (env T6000_TOTAL, default 1500).
"""
import os, sys, json
import numpy as np, torch, torch.nn as nn, torch.nn.functional as F
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import real_restore as rr

N = 906
C = rr.C
TOTAL = int(os.environ.get("T6000_TOTAL", "1500"))

class FloatRestorer(nn.Module):
    def __init__(self, c=C):
        super().__init__()
        self.body = nn.Sequential(
            nn.Conv2d(3, c, 3, padding=1), nn.ReLU(inplace=True),
            nn.Conv2d(c, c, 3, padding=1), nn.ReLU(inplace=True),
            nn.Conv2d(c, c, 3, padding=1), nn.ReLU(inplace=True),
            nn.Conv2d(c, 3, 3, padding=1))
    def forward(self, x):
        return x + self.body(x)

def make(kind):
    return rr.Restorer() if kind == "ternary" else FloatRestorer()

def lr_at(step):
    return 2e-3 * 0.5 * (1 + np.cos(np.pi * min(step, TOTAL) / TOTAL))

def train(kind, work, steps):
    torch.manual_seed(0); torch.set_num_threads(2)
    ref = rr.mm(os.environ["REF_RGB"], (N, rr.H, rr.W, 3)); unsq = rr.mm(f"{work}/unsq6000.u8", (N, rr.H, rr.W, 3))
    name = {"ternary": "t6000.pt", "float": "f6000.pt"}[kind]
    ck = f"{work}/{name}"
    net = make(kind); params = sum(p.numel() for p in net.parameters())
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
            y0, x0 = rng.integers(0, rr.H - crop), rng.integers(0, rr.W - crop)
            xs.append(unsq[f, y0:y0 + crop, x0:x0 + crop]); ys.append(ref[f, y0:y0 + crop, x0:x0 + crop])
        x = torch.from_numpy(np.stack(xs).astype(np.float32) / 255).permute(0, 3, 1, 2)
        y = torch.from_numpy(np.stack(ys).astype(np.float32) / 255).permute(0, 3, 1, 2)
        out = net(x)
        loss = F.l1_loss(out, y) + 2.0 * F.l1_loss(rr.sobel(out.mean(1, keepdim=True)), rr.sobel(y.mean(1, keepdim=True)))
        opt.zero_grad(); loss.backward(); opt.step()
        if (step + 1) % 500 == 0: print(f"{kind} step {step+1} loss {loss.item():.4f}", flush=True)
    torch.save({"net": net.state_dict(), "opt": opt.state_dict(), "step": start + steps}, ck)
    json.dump({"kind": kind, "params": params, "steps_done": start + steps, "total": TOTAL}, open(f"{work}/{name}.json", "w"))
    print(f"saved {kind} {start+steps} steps, params {params}", flush=True)

if __name__ == "__main__":
    train(sys.argv[1], sys.argv[2], int(sys.argv[3]))
