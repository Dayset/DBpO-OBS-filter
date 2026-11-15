"""Tables for research/stack/README.md from the result JSON and the per-frame arrays.

  report.py WORK          WORK = the directory given to run_all.sh (holds v4/*.npz and v4/motion.npy)
Dynamic use is simulated the way adaptive streaming already switches: both streams are encoded whole, with a closed
GOP every 2 s, and the viewer gets, segment by segment, the stream the policy picks. Demand is estimated from the
240x135 temporal metric with the calibration from research/foveation/starvation_findings.md (1.35 Mbit/s per unit).
"""
import json, os, sys
import numpy as np
S = os.path.dirname(os.path.abspath(__file__)); W = sys.argv[1]; SEG = 120

def table(name, cols=("kbps_actual", "psnr", "c_psnr", "vmaf", "vmaf_p5", "enc_cpu_s", "pre_cpu_s", "dec_cpu_s")):
    p = f"{S}/{name}.json"
    if not os.path.exists(p): return
    d = json.load(open(p)); print(f"\n### {name}\n\n| arm | kbit/s | " + " | ".join(cols) + " | ms/frame enc |\n|" + "---|" * (len(cols) + 3))
    for k, v in d.items():
        a, r = k.split("/")
        print(f"| {a} | {r} | " + " | ".join(str(v[c]) for c in cols) + f" | {1000 * v['enc_cpu_s'] / v['frames']:.1f} |")

for n in ("roi_v3", "control_v3", "full_v4", "server", "spins", "codecs"): table(n)

if os.path.exists(f"{W}/v4/motion.npy"):
    mot = np.load(f"{W}/v4/motion.npy"); n = len(mot) // SEG
    seg = lambda a: a[:n * SEG].reshape(n, SEG).mean(1)
    m = seg(mot)
    for rate in (6000, 12000):
        f = {a: f"{W}/v4/{a}_{rate}.npz" for a in ("plain", "squish56", "squish56c", "roi2zone", "scale810")}
        if not (os.path.exists(f["plain"]) and os.path.exists(f["squish56"])): continue
        A = {a: {k: seg(v) for k, v in np.load(p).items()} for a, p in f.items() if os.path.exists(p)}
        P, Q = A["plain"], A.get("squish56c", A["squish56"])  # squish56c = bicubic unsquish, as the downscale arm gets
        gain = Q["vmaf"] - P["vmaf"]
        print(f"\n### dynamic, {rate} kbit/s, {n} segments of 2 s, squish arm = {'squish56c' if 'squish56c' in A else 'squish56'}\n")
        print(f"squish wins on VMAF in {(gain > 0).sum()} of {n} segments, on whole PSNR in {(Q['psnr'] > P['psnr']).sum()}, "
              f"on centre PSNR in {(Q['c_psnr'] > P['c_psnr']).sum()}; correlation of VMAF gain with the motion metric "
              f"{np.corrcoef(m, gain)[0, 1]:.2f}; gain range {gain.min():+.1f} to {gain.max():+.1f}\n")
        print("| policy | segments squished | VMAF mean | VMAF worst 10% of segments | PSNR | centre PSNR | kbit/s | share of oracle gain |\n|---|---|---|---|---|---|---|---|")
        orc = gain > 0
        def row(name, pick, arm=None, alt=None):
            alt = Q if alt is None else alt
            g = lambda k: np.where(pick, alt[k], P[k]) if arm is None else A[arm][k]
            v = g("vmaf"); worst = np.sort(v)[:max(1, n // 10)].mean()
            share = (v.mean() - P["vmaf"].mean()) / (np.where(orc, Q["vmaf"], P["vmaf"]).mean() - P["vmaf"].mean())
            print(f"| {name} | {int(pick.sum()) if arm is None else '-'} | {v.mean():.2f} | {worst:.2f} | {g('psnr').mean():.2f} | "
                  f"{g('c_psnr').mean():.2f} | {g('bytes').mean() * 8 * 60 / 1000:.0f} | {100 * share:.0f}% |")
        row("always plain", np.zeros(n, bool)); row("always squish", np.ones(n, bool))
        if "roi2zone" in A: row("always ROI (2 zones)", None, "roi2zone")
        if "scale810" in A:
            D = A["scale810"]; row("always plain downscale to 1440x810", None, "scale810")
            print(f"| squish minus downscale, per segment: VMAF {np.mean(Q['vmaf'] - D['vmaf']):+.2f} (wins {(Q['vmaf'] > D['vmaf']).sum()} of {n}), "
                  f"PSNR {np.mean(Q['psnr'] - D['psnr']):+.2f}, centre PSNR {np.mean(Q['c_psnr'] - D['c_psnr']):+.2f} (wins {(Q['c_psnr'] > D['c_psnr']).sum()}) | | | | | | | |")
        row("oracle (better VMAF per segment)", orc)
        ratio = 1.35 * m / (rate / 1000)  # estimated demand over budget
        for x in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
            row(f"squish when demand/budget >= {x}, this segment", ratio >= x)
        for x in (1.0, 1.5, 2.0, 2.5, 3.0):
            row(f"squish when demand/budget >= {x}, previous segment", np.concatenate([[False], ratio[:-1] >= x]))
        if "scale810" in A:
            for x in (1.5, 2.0, 2.5):
                row(f"downscale to 1440x810 when demand/budget >= {x}, this segment", ratio >= x, alt=A["scale810"])
