"""R9 sharpening-neutral check, applied to a cnn_sweep.py summary (no new encodes).

  python -I r9_check.py SUMMARY.txt

Reads the table printed by `cnn_sweep.py WORK summary`. For every codec/rate/arm it:
  - takes bicubic's centre PSNR as the reference,
  - rejects any recipe whose centre PSNR is more than 0.3 dB below bicubic (R9 rule),
  - reports the best whole-frame VMAF among recipes that pass, and the best overall.
VMAF-NEG is not in the summary; the verdict is provisional until it is computed.
"""
import re, sys
from collections import defaultdict

GUARD_DB = 0.3
ROW = re.compile(r"^(\S+) (\d+) (\S+) (\S+) \| (\d+) \| ([\d.]+) \|")
CENTRE = re.compile(r"\| ([\d.]+) \| ([\d.]+)\s*$")

rows = []
for line in open(sys.argv[1], encoding="utf-8"):
    m = ROW.match(line.strip())
    if not m:
        continue
    c = CENTRE.search(line.strip())
    rows.append(dict(codec=m[1], rate=int(m[2]), arm=m[3], recipe=m[4],
                     vmaf=float(m[6]), centre=float(c[1]) if c else None))

groups = defaultdict(dict)
for r in rows:
    groups[(r["codec"], r["rate"], r["arm"])][r["recipe"]] = r

print(f"{'codec':<12}{'rate':>6} {'arm':<9}{'best overall':<22}{'pass?':<7}{'best passing':<22}")
for key in sorted(groups):
    g = groups[key]
    base = g.get("bicubic")
    if not base or base["centre"] is None:
        continue
    passing = {k: v for k, v in g.items() if v["centre"] is not None and v["centre"] >= base["centre"] - GUARD_DB}
    best = max(g.values(), key=lambda v: v["vmaf"])
    best_pass = max(passing.values(), key=lambda v: v["vmaf"]) if passing else None
    ok = best["centre"] >= base["centre"] - GUARD_DB
    print(f"{key[0]:<12}{key[1]:>6} {key[2]:<9}{best['recipe'] + ' ' + format(best['vmaf'], '.2f'):<22}"
          f"{'yes' if ok else 'NO':<7}{(best_pass['recipe'] + ' ' + format(best_pass['vmaf'], '.2f')) if best_pass else '-':<22}")

print(f"\nguard: centre PSNR >= bicubic - {GUARD_DB} dB. VMAF-NEG: not in summary, pending.")
