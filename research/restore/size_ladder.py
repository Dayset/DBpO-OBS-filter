"""Size ladder for the restorer (issue #25.1). Depth and kernels are fixed (three 3x3 convs, then the 3x3 output
conv), so the receptive field is the same at every size; only the width C changes.

    python3 research/restore/size_ladder.py            # prints params and MACs per source pixel, writes configs

Parameters for a k x k upscale (k = 2 for 540, 3 for 360):  conv1 3*C*9+C, conv2 and conv3 C*C*9+C each,
conv4 C*3k^2*9+3k^2. MACs per source pixel: the four conv weight counts (biases excluded), since all convs run
at source resolution.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TARGETS = {"5k": 5000, "10k": 10000, "50k": 50000, "200k": 200000}


def params(c, k=2):
    o = 3 * k * k
    return (3 * c * 9 + c) + 2 * (c * c * 9 + c) + (c * o * 9 + o)


def macs(c, k=2):
    o = 3 * k * k
    return 3 * c * 9 + 2 * c * c * 9 + c * o * 9


def width_for(target, k=2):
    return min(range(1, 400), key=lambda c: abs(params(c, k) - target))


if __name__ == "__main__":
    rows = {}
    for name, t in TARGETS.items():
        c = width_for(t)
        rows[name] = {"width": c, "params": params(c), "macs_per_src_px": macs(c)}
        print(f"{name:>5}: C={c:3d}  params={params(c):7d}  MACs/source px={macs(c):7d}", flush=True)
        cfg = {"name": f"ladder_{name}_L540", "level": "540", "format": "float", "width": c, "steps": 2000,
               "bs": 12, "crop": 40, "lr": 0.002, "every": 100, "seed": 0, "edge_w": 2.0,
               "data": "research/restore/out/ps2train/manifest.json", "init": None}
        json.dump(cfg, open(os.path.join(HERE, "configs", f"ladder_{name}.json"), "w"), indent=2)
    json.dump(rows, open(os.path.join(HERE, "configs", "ladder_sizes.json"), "w"), indent=2)
