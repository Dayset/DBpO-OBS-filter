"""CPU reference model (numpy) of data/directional_object_blur.effect: JFA dilation + time-slice gather.
Keep in sync with the shader. Also a CPU reference of the shader's old vs new motion-blur reconstruction, compared
with ground truth (a real box-shutter exposure averaged from sub-frames).

Scene: textured static background, a bright textured box moving right with
speed V px/frame. Motion field = what Combine would output (blur vector =
velocity * strength) on the box at its end-of-frame position, 0 elsewhere.
Flow resolution == full resolution here (flow_scale 1) to isolate the method.
"""
import numpy as np
from scipy.ndimage import map_coordinates

H, W = 64, 220
rng = np.random.default_rng(3)
bg = 0.25 + 0.15 * rng.random((H, W))
box_tex = 0.75 + 0.2 * rng.random((24, 30))


def render(x0):
    """Exact area coverage: each pixel = sum(tex * overlap) + bg * (1 - total)."""
    img = bg.copy()
    xs = np.arange(W)[None, :]
    lefts = x0 + np.arange(30)[:, None]
    ov = np.clip(np.minimum(xs + 1, lefts + 1) - np.maximum(xs, lefts), 0, 1)  # (30, W)
    total = ov.sum(0)
    box = box_tex @ ov  # (24, W)
    img[20:44, :] = img[20:44, :] * (1 - total) + box
    return img


def ground_truth(x_end, v, phase, k=400):
    # exposure spans [-(1-phase), +phase] frames around the frame time
    acc = np.zeros((H, W))
    for i in range(k):
        tau = -(1 - phase) + (i + 0.5) / k
        acc += render(x_end + v * tau)
    return acc / k


def motion_field(x_end, v):
    m = np.zeros((H, W, 2))
    m[20:44, int(round(x_end)):int(round(x_end)) + 30, 0] = v
    return m


def sample(img, y, x):
    return map_coordinates(img, [np.clip(y, 0, H - 1), np.clip(x, 0, W - 1)], order=1, mode="nearest")


def sample_vec(m, y, x):
    return np.stack([sample(m[..., 0], y, x), sample(m[..., 1], y, x)], -1)


# ---------------------------------------------------------------- old method
def old_blur(img, m, n=24, nr=4):
    # neighbor max (square radius nr) + centered McGuire-style gather
    mag = np.hypot(m[..., 0], m[..., 1])
    vn = np.zeros_like(m)
    best = np.full((H, W), -1.0)
    for dy in range(-nr, nr + 1):
        for dx in range(-nr, nr + 1):
            ys = np.clip(np.arange(H)[:, None] + dy, 0, H - 1)
            xs = np.clip(np.arange(W)[None, :] + dx, 0, W - 1)
            cand = mag[ys, xs]
            upd = cand > best
            best = np.where(upd, cand, best)
            vn = np.where(upd[..., None], m[ys, xs], vn)
    Y, X = np.mgrid[0:H, 0:W].astype(float)
    vn_len = np.hypot(vn[..., 0], vn[..., 1])
    vc_len = np.maximum(np.hypot(m[..., 0], m[..., 1]), 0.5)
    cone = lambda d, s: np.clip(1 - d / np.maximum(s, 1e-3), 0, 1)
    wc = 1 / vc_len
    acc = img * wc
    wsum = wc.copy()
    for i in range(n):
        t = -0.5 + (i + 0.5) / n
        sy, sx = Y + vn[..., 1] * t, X + vn[..., 0] * t
        vs = sample_vec(m, sy, sx)
        vs_len = np.maximum(np.hypot(vs[..., 0], vs[..., 1]), 0.5)
        dist = abs(t) * vn_len
        w = cone(dist, vs_len) / vs_len + cone(dist, vc_len) / vc_len
        acc += sample(img, sy, sx) * w
        wsum += w
    out = acc / wsum
    return np.where(vn_len < 1.0, img, out)


# ---------------------------------------------------------------- new method
def jfa(m, phase, max_len):
    seeds = np.zeros((H, W, 4))  # off_x, off_y, vel_x, vel_y
    moving = np.hypot(m[..., 0], m[..., 1]) >= 1.0
    seeds[..., 2:] = np.where(moving[..., None], m, 0)

    def covers(d, vel):
        l2 = (vel ** 2).sum(-1)
        safe = np.maximum(l2, 1e-9)
        t = (d * vel).sum(-1) / safe
        margin = 0.75 / np.sqrt(safe)
        side = d - vel * t[..., None]
        ok = (t >= -phase - margin) & (t <= 1 - phase + margin) & ((side ** 2).sum(-1) <= 1.0)
        return np.where(l2 < 1e-4, (d ** 2).sum(-1) < 0.25, ok)

    k = 1
    while k < max_len:
        k <<= 1
    steps = []
    while k >= 1:
        steps.append(k)
        k //= 2
    steps.append(1)
    for k in steps:
        best = seeds.copy()
        has = (best[..., 2:] ** 2).sum(-1) > 0
        bspeed = np.where(has & covers(best[..., :2], best[..., 2:]), np.hypot(best[..., 2], best[..., 3]), 0.0)
        best = np.where((bspeed > 0)[..., None], best, 0)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                ys = np.arange(H)[:, None] + dy * k
                xs = np.arange(W)[None, :] + dx * k
                inb = (ys >= 0) & (ys < H) & (xs >= 0) & (xs < W)
                c = seeds[np.clip(ys, 0, H - 1), np.clip(xs, 0, W - 1)]
                sp = np.hypot(c[..., 2], c[..., 3])
                d = np.stack([dx * k + c[..., 0], dy * k + c[..., 1]], -1)
                take = inb & (sp > bspeed) & covers(d, c[..., 2:])
                best = np.where(take[..., None], np.concatenate([d, c[..., 2:]], -1), best)
                bspeed = np.where(take, sp, bspeed)
        seeds = best
    return seeds[..., 2:]


def new_blur(img, m, phase, n=24, tail_fade=0.0, highlight=0.0):
    vn = jfa(m, phase, max_len=64)
    vc = m
    pick = (vc ** 2).sum(-1) > (vn ** 2).sum(-1)
    vn = np.where(pick[..., None], vc, vn)
    vn_len = np.hypot(vn[..., 0], vn[..., 1])
    dirv = vn / np.maximum(vn_len, 1e-6)[..., None]
    own = np.maximum((vc * dirv).sum(-1), 0)
    own_ahead, own_behind = own * (1 - phase), own * phase
    Y, X = np.mgrid[0:H, 0:W].astype(float)
    hw = lambda c: 1 + highlight * np.clip((c - 0.6) / 0.4, 0, 1) ** 2 * (3 - 2 * np.clip((c - 0.6) / 0.4, 0, 1))
    covered = lambda d, r: np.where(r > 0.25, np.clip(r - d + 0.5, 0, 1), 0.0)
    hc = hw(img)
    acc = np.zeros((H, W))
    wacc = np.zeros((H, W))
    for i in range(n):
        t = -phase + (i + 0.5) / n
        dist = abs(t) * vn_len
        sy, sx = Y + vn[..., 1] * t, X + vn[..., 0] * t
        vs = sample_vec(m, sy, sx)
        reach = np.maximum((vs * dirv).sum(-1), 0) * ((1 - phase) if t >= 0 else phase)
        c = np.maximum(covered(dist, reach), covered(dist, own_ahead if t >= 0 else own_behind))
        age = t / max(1 - phase, 1e-3) if t >= 0 else 0.0
        sl = 1 - tail_fade * age
        cs = sample(img, sy, sx)
        hs = hw(cs)
        acc += sl * (c * hs * cs + (1 - c) * hc * img)
        wacc += sl * (c * hs + (1 - c) * hc)
    out = acc / np.maximum(wacc, 1e-5)
    return np.where(vn_len < 1.0, img, out)


def report(name, est, gt, frame):
    rows = slice(20, 44)
    err = np.sqrt(np.mean((est[rows] - gt[rows]) ** 2))
    base = np.sqrt(np.mean((frame[rows] - gt[rows]) ** 2))
    print(f"  {name:<34} RMSE {err:.4f}   ({(1 - err / base) * 100:5.1f}% closer to the real exposure than no blur)")
    return err


x_end = 140.0
all_ok = True
for v, phase in ((12.0, 0.0), (40.0, 0.0), (90.0, 0.0), (40.0, 0.5)):
    frame = render(x_end)
    m = motion_field(x_end, v)
    gt = ground_truth(x_end, v, phase)
    kind = "trailing" if phase == 0 else "centered"
    print(f"speed {v:>4.0f} px/frame, {kind} shutter:")
    e_old = report("old (neighbor-max + centered)", old_blur(frame, m), gt, frame)
    e_new = report("new (jump flood + time slices)", new_blur(frame, m, phase), gt, frame)
    all_ok &= e_new < e_old

# where does blur land? (trailing, v=40) rows 30, compare columns ahead/behind
frame = render(x_end)
m = motion_field(x_end, 40.0)
gt = ground_truth(x_end, 40.0, 0.0)
nb = new_blur(frame, m, 0.0)
ob = old_blur(frame, m)
ahead = slice(int(x_end) + 31, int(x_end) + 60)
behind = slice(int(x_end) - 40, int(x_end))
dev = lambda img, s: np.abs(img[30, s] - frame[30, s]).mean()
print("\ntrailing, 40 px/frame: mean change vs. unblurred frame")
print(f"  ahead of the box  : truth {dev(gt, ahead):.3f}  old {dev(ob, ahead):.3f}  new {dev(nb, ahead):.3f}")
print(f"  behind the box    : truth {dev(gt, behind):.3f}  old {dev(ob, behind):.3f}  new {dev(nb, behind):.3f}")
all_ok &= dev(nb, ahead) < 0.01 and abs(dev(nb, behind) - dev(gt, behind)) < 0.03

tf = new_blur(frame, m, 0.0, tail_fade=0.8)
hl = new_blur(frame, m, 0.0, highlight=2.0)
print(f"\ntail fade 80%: trail brightness near box {tf[30, int(x_end)-5]:.3f}, far end {tf[30, int(x_end)-35]:.3f} (should fade)")
print(f"highlights 2x: trail brightness near box {hl[30, int(x_end)-5]:.3f} vs plain {nb[30, int(x_end)-5]:.3f} (should be brighter)")
all_ok &= tf[30, int(x_end) - 35] < nb[30, int(x_end) - 35] and hl[30, int(x_end) - 5] > nb[30, int(x_end) - 5]
print("\nRESULT:", "PASS" if all_ok else "FAIL")
raise SystemExit(0 if all_ok else 1)
