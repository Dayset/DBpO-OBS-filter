"""Int8 post-training quantisation of a trained restorer (issue #25.4).

    python3 research/restore/quantize_i8.py out/restorer_ladder_10k_L540.rstr

Writes <name>_i8.rstr next to it: weights per tensor as int8 (scale = max|w| / 127), biases float32, the same RSTR
file format. restore_math.load() dequantises it, so the evaluation runs the int8 weights exactly.
Prints the largest weight error per tensor (relative to the tensor's max) as a check.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import restore_math as rm  # noqa: E402


def quantise(src):
    t = rm.load(src)
    dst = src.replace(".rstr", "_i8.rstr")
    rm.export_state(t, dst, "i8")
    q = rm.load(dst)
    worst = max(float(np.abs(q[k] - t[k]).max() / max(np.abs(t[k]).max(), 1e-12)) for k in t if k.endswith(".w"))
    print(f"{os.path.basename(dst)}: worst weight error {worst:.4f} of tensor max", flush=True)
    return dst


if __name__ == "__main__":
    for p in sys.argv[1:]:
        quantise(p)
