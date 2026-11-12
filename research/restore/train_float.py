"""Resumable float trainer for the edge restorer (issue #22.3, spec: SPEC.md §3).

    python3 research/restore/train_float.py --level 540 --steps 2000
    python3 research/restore/train_float.py --level 540 --steps 2000 --export out/restorer_L540.rstr

Trains on the training clips (ids 4-11) written by `restore_bench.py make`. Checkpoints to
out/ckpt_L{level}.pt every --every steps; rerunning the same command resumes from it.
Loss as in the prototype: L1 + 2 x L1 on Sobel gradient magnitude.
"""
import argparse
import json
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


class TernaryConv2d(nn.Conv2d):
    """BitNet b1.58-style (issue #22.5): weights rounded to {-s, 0, +s} per layer in the forward
    pass, s = mean |w|; straight-through gradients to the float weights."""

    def ternary_weight(self):
        w = self.weight
        s = w.abs().mean().clamp(min=1e-5)
        return torch.clamp(torch.round(w / s), -1, 1) * s

    def forward(self, x):
        w = self.weight
        wq = self.ternary_weight()
        return F.conv2d(x, w + (wq - w).detach(), self.bias, self.stride, self.padding)


class Restorer(nn.Module):
    """SPEC.md §3: three 3x3 convs (ReLU), conv to 3*k^2, pixel shuffle, add the bicubic base."""

    def __init__(self, k, fmt="float"):
        super().__init__()
        self.k = k
        conv = TernaryConv2d if fmt == "ternary" else nn.Conv2d
        self.conv1 = conv(3, C, 3, padding=1)
        self.conv2 = conv(C, C, 3, padding=1)
        self.conv3 = conv(C, C, 3, padding=1)
        self.conv4 = conv(C, 3 * k * k, 3, padding=1)

    def forward(self, x):
        h = F.relu(self.conv3(F.relu(self.conv2(F.relu(self.conv1(x))))))
        base = F.interpolate(x, scale_factor=self.k, mode="bicubic", align_corners=False)
        return base + F.pixel_shuffle(self.conv4(h), self.k)


def sobel(x):
    g = x.mean(1, keepdim=True)
    gx, gy = F.conv2d(g, KX, padding=1), F.conv2d(g, KY, padding=1)
    return torch.sqrt(gx * gx + gy * gy + 1e-8)


def export_dict(model, fmt):
    """Float weights, or the ternary-rounded weights for --format ternary (what the forward pass uses)."""
    sd = {}
    for name in ("conv1", "conv2", "conv3", "conv4"):
        m = getattr(model, name)
        w = m.ternary_weight() if fmt == "ternary" else m.weight
        sd[f"{name}.w"] = w.detach().numpy()
        sd[f"{name}.b"] = m.bias.detach().numpy()
    sd["_width"] = C
    return sd


class Frames:
    """Lazy read-only view over .npy shards (memmap), so memory stays flat whatever the dataset size.
    frames[i] is one (H, W, 3) uint8 frame; len() and shape work as for one array."""

    def __init__(self, paths):
        self.arrs = [np.load(p, mmap_mode="r") for p in paths]
        self.cum = np.cumsum([0] + [len(a) for a in self.arrs])
        self.shape = (int(self.cum[-1]),) + self.arrs[0].shape[1:]

    def __len__(self):
        return self.shape[0]

    def __getitem__(self, i):
        k = np.searchsorted(self.cum, i, side="right") - 1
        return self.arrs[k][i - self.cum[k]]


def load_shards(manifest, level):
    """Frames for the shards of one level in a restore_data.py manifest (paths relative to its folder)."""
    base = os.path.dirname(manifest)
    shards = [x for x in json.load(open(manifest))["shards"] if x["level"] == level]
    if not shards:
        raise SystemExit(f"no shards for level {level} in {manifest}")
    lo = Frames([os.path.join(base, x["lo"]) for x in shards])
    gt = Frames([os.path.join(base, x["gt"]) for x in shards])
    return lo, gt


def load_pairs(level):
    lo = np.concatenate([np.load(f"{OUT}/pairs/clip{c}_L{level}.npy")[:T] for c in TRAIN_CLIPS])
    gt = np.concatenate([np.load(f"{OUT}/gt/clip{c}.npy")[:T] for c in TRAIN_CLIPS])
    assert gt.shape[1] == lo.shape[1] * SCALE[level], (gt.shape, lo.shape)
    return lo, gt


def batch(lo, gt, rng, k, bs, crop):
    xs, ys = [], []
    for i in rng.integers(0, len(lo), bs):
        y0, x0 = rng.integers(0, lo.shape[1] - crop), rng.integers(0, lo.shape[2] - crop)
        xs.append(lo[i][y0:y0 + crop, x0:x0 + crop])
        ys.append(gt[i][y0 * k:(y0 + crop) * k, x0 * k:(x0 + crop) * k])
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
    ap.add_argument("--steps", type=int, default=2000, help="total schedule length (cosine decay)")
    ap.add_argument("--until", type=int, help="stop this invocation at this step (default: --steps)")
    ap.add_argument("--bs", type=int, default=12)
    ap.add_argument("--crop", type=int, default=40)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--every", type=int, default=100)
    ap.add_argument("--format", default="float", choices=["float", "ternary"])
    ap.add_argument("--data", help="restore_data.py manifest to train on instead of the synthetic pairs")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--edge_w", type=float, default=2.0, help="weight of the Sobel-gradient loss term")
    ap.add_argument("--name", help="checkpoint / loss-log name (default L{level} plus format tag)")
    ap.add_argument("--config", help="JSON file of defaults for these options (command-line flags override it)")
    pre, _ = ap.parse_known_args()
    if pre.config:
        ap.set_defaults(**json.load(open(pre.config)))
    ap.add_argument("--export", help="write the RSTR v1 file here when training finishes")
    a = ap.parse_args()
    torch.set_num_threads(2)
    k = SCALE[a.level]
    lo, gt = load_shards(a.data, a.level) if a.data else load_pairs(a.level)
    tag = "" if a.format == "float" else "_" + a.format
    name = a.name or f"L{a.level}{tag}"
    ckpt = os.path.join(OUT, f"ckpt_{name}.pt")
    torch.manual_seed(a.seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = Restorer(k, a.format).to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=a.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, a.steps)
    rng, step = np.random.default_rng(a.seed), 0
    if os.path.exists(ckpt):
        ck = torch.load(ckpt, weights_only=False, map_location=dev)
        model.load_state_dict(ck["model"]); opt.load_state_dict(ck["opt"]); sched.load_state_dict(ck["sched"])
        rng.bit_generator.state, step = ck["rng"], ck["step"]
        print(f"resumed from step {step}", flush=True)
    until = a.until or a.steps
    log = open(os.path.join(OUT, f"loss_{name}.csv"), "a")
    while step < until:
        x, y = (t.to(dev) for t in batch(lo, gt, rng, k, a.bs, a.crop))
        out = model(x)
        loss = F.l1_loss(out, y) + a.edge_w * F.l1_loss(sobel(out), sobel(y))
        opt.zero_grad(); loss.backward(); opt.step(); sched.step(); step += 1
        log.write(f"{step},{loss.item():.6f}\n")
        if step % a.every == 0 or step == until:
            log.flush()
            save(ckpt, model, opt, sched, step, rng)
            print(f"step {step} loss {loss.item():.4f}", flush=True)
    log.close()
    print(f"device {dev}, params {sum(p.numel() for p in model.parameters())}", flush=True)
    if a.export:
        rm.export_state(export_dict(model, a.format), a.export, "f32")
        print("exported", a.export, flush=True)


if __name__ == "__main__":
    main()
