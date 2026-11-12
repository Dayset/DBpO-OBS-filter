"""Resumable float trainer for the edge restorer (issue #22.3, spec: SPEC.md §3).

    python3 research/restore/train_float.py --level 540 --steps 2000
    python3 research/restore/train_float.py --level 540 --steps 2000 --export out/restorer_L540.rstr

Trains on the training clips (ids 4-11) written by `restore_bench.py make`. Checkpoints to
out/ckpt_L{level}.pt every --every steps; rerunning the same command resumes from it.
Loss as in the prototype: L1 + 2 x L1 on Sobel gradient magnitude.
"""
import argparse
import os
import sys

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import restore_math as rm  # noqa: E402
from restore_bench import OUT, SCALE, T  # noqa: E402

C = 20
TRAIN_CLIPS = range(4, 12)
KX = torch.tensor([[-1.0, 0, 1], [-2, 0, 2], [-1, 0, 1]]).view(1, 1, 3, 3) / 8
KY = KX.transpose(2, 3)


class Restorer(nn.Module):
    """SPEC.md §3: three 3x3 convs (ReLU), conv to 3*k^2, pixel shuffle, add the bicubic base."""

    def __init__(self, k):
        super().__init__()
        self.k = k
        self.conv1 = nn.Conv2d(3, C, 3, padding=1)
        self.conv2 = nn.Conv2d(C, C, 3, padding=1)
        self.conv3 = nn.Conv2d(C, C, 3, padding=1)
        self.conv4 = nn.Conv2d(C, 3 * k * k, 3, padding=1)

    def forward(self, x):
        h = F.relu(self.conv3(F.relu(self.conv2(F.relu(self.conv1(x))))))
        base = F.interpolate(x, scale_factor=self.k, mode="bicubic", align_corners=False)
        return base + F.pixel_shuffle(self.conv4(h), self.k)


def sobel(x):
    g = x.mean(1, keepdim=True)
    gx, gy = F.conv2d(g, KX, padding=1), F.conv2d(g, KY, padding=1)
    return torch.sqrt(gx * gx + gy * gy + 1e-8)


def load_pairs(level):
    lo = np.concatenate([np.load(f"{OUT}/pairs/clip{c}_L{level}.npy")[:T] for c in TRAIN_CLIPS])
    gt = np.concatenate([np.load(f"{OUT}/gt/clip{c}.npy")[:T] for c in TRAIN_CLIPS])
    assert gt.shape[1] == lo.shape[1] * SCALE[level], (gt.shape, lo.shape)
    return lo, gt


def batch(lo, gt, rng, k, bs, crop):
    xs, ys = [], []
    for i in rng.integers(0, len(lo), bs):
        y0, x0 = rng.integers(0, lo.shape[1] - crop), rng.integers(0, lo.shape[2] - crop)
        xs.append(lo[i, y0:y0 + crop, x0:x0 + crop])
        ys.append(gt[i, y0 * k:(y0 + crop) * k, x0 * k:(x0 + crop) * k])
    to_t = lambda a: torch.from_numpy(np.stack(a).astype(np.float32) / 255).permute(0, 3, 1, 2)
    return to_t(xs), to_t(ys)


def save(path, model, opt, sched, step, rng):
    tmp = path + ".tmp"
    torch.save({"model": model.state_dict(), "opt": opt.state_dict(), "sched": sched.state_dict(),
                "step": step, "rng": rng.bit_generator.state}, tmp)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", default="540", choices=list(SCALE))
    ap.add_argument("--steps", type=int, default=2000)
    ap.add_argument("--bs", type=int, default=12)
    ap.add_argument("--crop", type=int, default=40)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--every", type=int, default=250)
    ap.add_argument("--export", help="write the RSTR v1 file here when training finishes")
    a = ap.parse_args()
    torch.set_num_threads(2)
    k = SCALE[a.level]
    lo, gt = load_pairs(a.level)
    ckpt = os.path.join(OUT, f"ckpt_L{a.level}.pt")
    torch.manual_seed(0)
    model = Restorer(k)
    opt = torch.optim.Adam(model.parameters(), lr=a.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, a.steps)
    rng, step = np.random.default_rng(0), 0
    if os.path.exists(ckpt):
        ck = torch.load(ckpt, weights_only=False)
        model.load_state_dict(ck["model"]); opt.load_state_dict(ck["opt"]); sched.load_state_dict(ck["sched"])
        rng.bit_generator.state, step = ck["rng"], ck["step"]
        print(f"resumed from step {step}", flush=True)
    while step < a.steps:
        x, y = batch(lo, gt, rng, k, a.bs, a.crop)
        out = model(x)
        loss = F.l1_loss(out, y) + 2.0 * F.l1_loss(sobel(out), sobel(y))
        opt.zero_grad(); loss.backward(); opt.step(); sched.step(); step += 1
        if step % 50 == 0:
            print(f"step {step} loss {loss.item():.4f}", flush=True)
        if step % a.every == 0 or step == a.steps:
            save(ckpt, model, opt, sched, step, rng)
    print(f"params {sum(p.numel() for p in model.parameters())}", flush=True)
    if a.export:
        sd = {f"{n.split('.')[0]}.{'w' if n.endswith('weight') else 'b'}": p.detach().numpy()
              for n, p in model.state_dict().items()}
        sd["_width"] = C
        rm.export_state(sd, a.export, "f32")
        print("exported", a.export, flush=True)


if __name__ == "__main__":
    main()
