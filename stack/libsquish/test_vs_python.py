"""Compare dbpo-squish with the Python reference (research/foveation/squish_math.py, area-filtered separable) on the luma plane.
usage: test_vs_python.py in.y4m out.y4m W H w h  -> prints mean/max abs difference (8-bit levels) of the first frame."""
import sys, numpy as np
from scipy import sparse
sys.path.insert(0, "/home/claude/dbpo-obs-filter/research/foveation")
from squish_math import Axis
def y(path, w, h):
    f = open(path, "rb"); f.readline(); f.readline()
    return np.frombuffer(f.read(w * h), np.uint8).reshape(h, w).astype(np.float32)
def mat(n, m):
    ax = Axis(r=m / n, c=.15, f=.5, p=1.0); ue = ax.forward(np.linspace(0, 1, n + 1)) * m; j = np.arange(m)
    ov = np.clip(np.minimum((j + 1)[:, None], ue[None, 1:]) - np.maximum(j[:, None], ue[None, :-1]), 0, None)
    return sparse.csr_matrix((ov / ov.sum(1, keepdims=True)).astype(np.float32))
a, b, W, H, w, h = sys.argv[1], sys.argv[2], *map(int, sys.argv[3:7])
ref = np.clip(mat(H, h) @ (mat(W, w) @ y(a, W, H).T).T + .5, 0, 255).astype(np.uint8).astype(np.float32)
d = np.abs(ref - y(b, w, h)); print(f"mean {d.mean():.3f}  max {d.max():.0f}  (levels of 255)")
