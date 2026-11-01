"""Checks the C mapping (stdin: test_squish_math output) against the Python reference."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "research", "foveation"))
from squish_math import Axis  # noqa: E402

CASES = [Axis(0.75, 0.15, 0.5, 1.0), Axis(0.75, 0.15, 0.5, 0.5), Axis(0.65, 0.15, 0.5, 0.75), Axis(0.7, 0.1, 0.35, 1.0),
         Axis(0.9, 0.3, 0.6, 2.0)]
worst = 0.0
rows = [line.split() for line in sys.stdin if line.strip()]
for c, x, fwd, den, inv in rows:
    a = CASES[int(c)]
    x = float(x)
    worst = max(worst, abs(a.forward(x) - float(fwd)), abs(a.density(x) - float(den)), abs(a.inverse(x) - float(inv)))
print(f"{sys.argv[1] if len(sys.argv) > 1 else 'C'} vs Python reference: {len(rows)} values, worst difference {worst:.2e}",
      "PASS" if worst < 1e-9 and len(rows) == 105 else "FAIL")
sys.exit(0 if worst < 1e-9 and len(rows) == 105 else 1)
