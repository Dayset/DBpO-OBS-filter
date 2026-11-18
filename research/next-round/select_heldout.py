"""Choose the settings for the held-out run from TUNING clips only, by a fixed rule (written before any held-out result).
  python select_heldout.py RESULTS.jsonl PLAN.json ENV.bat

Per codec and rate (x264: 1500, 3000, 6000; hevc_nvenc: 3000, 6000), over the tuning clips, cells within 10% of the target rate:
  gap_centre = centre-50% VMAF of the arm minus the best downscale rung on the same clip (best by centre-50%)
  gap_whole  = whole-frame VMAF minus the whole-frame VMAF of that same best rung
An arm is eligible if it exists on all four tuning clips. Its score is the MEDIAN over clips.
  method (balanced): the eligible sq arm with the largest median (gap_centre + gap_whole), an equal-weight trade of centre and whole frame
  method (centre):   the eligible sq arm with the largest median gap_centre, no whole-frame condition
  ROI (x264 only):   the roi_ arm with the largest median (gap_centre + gap_whole)
  method+ROI:        the same rule over sq..._roiNN arms
Writes the plan, and a .bat with ONLY_ARMS (exact arm names) and ROI_SQ for run_heldout.bat.
"""
import json, sys, statistics as st, collections
TUNING = {"ref60", "Planetside_2_2026_11_15___21_29_34_08", "PlanetsideDifferentSpeed", "Planetside_2_2026_11_15___21_26_27_05"}
RATES = {"x264": [1500, 3000, 6000], "hevc_nvenc": [3000, 6000]}
rows = [json.loads(l) for l in open(sys.argv[1])]
rows = [r for r in rows if not r.get("infeasible") and r["clip"] in TUNING and abs(r["kbps"] - r["kbps_target"]) <= 0.1 * r["kbps_target"]]
v = lambda r, k: r["vmaf"][k]
plan, names = {}, set()
for codec, rates in RATES.items():
    for rate in rates:
        cells = collections.defaultdict(dict)
        for r in rows:
            if r["codec"] == codec and r["kbps_target"] == rate: cells[r["clip"]][r["arm"]] = r
        if len(cells) < len(TUNING): plan[f"{codec}_{rate}"] = "incomplete: only %d tuning clips with valid cells" % len(cells); continue
        gaps = collections.defaultdict(lambda: ([], []))
        for clip, arms in cells.items():
            sc = [a for n, a in arms.items() if n.startswith("scale")]
            if not sc: continue
            ds = max(sc, key=lambda a: v(a, "0.5_vmaf"))
            for n, a in arms.items():
                if n == "plain" or n.startswith("scale"): continue
                gaps[n][0].append(v(a, "0.5_vmaf") - v(ds, "0.5_vmaf")); gaps[n][1].append(v(a, "1.0_vmaf") - v(ds, "1.0_vmaf"))
        elig = {n: (st.median(g[0]), st.median(g[1])) for n, g in gaps.items() if len(g[0]) == len(TUNING)}
        def pick(pred, balanced=True):
            c = {n: s for n, s in elig.items() if pred(n)}
            return max(c, key=lambda n: c[n][0] + (c[n][1] if balanced else 0)) if c else None
        sq = lambda n: n.startswith("sq") and "_roi" not in n
        combo = lambda n: n.startswith("sq") and "_roi" in n
        roi = lambda n: n.startswith("roi_")
        choice = {"method_balanced": pick(sq), "method_centre": pick(sq, False)}
        if codec == "x264": choice.update({"roi_balanced": pick(roi), "method_roi_balanced": pick(combo)})
        plan[f"{codec}_{rate}"] = {k: {"arm": n, "median_gap_centre50": round(elig[n][0], 1), "median_gap_whole": round(elig[n][1], 1)} if n else None for k, n in choice.items()}
        names.update(n for n in choice.values() if n)
json.dump(plan, open(sys.argv[2], "w"), indent=1)
roi_sq = sorted({n.rsplit("_roi", 1)[0] for n in names if "_roi" in n and n.startswith("sq")})
with open(sys.argv[3], "w") as f:
    f.write("set ONLY_ARMS=" + ",".join(sorted(names)) + "\n")
    f.write("set ROI_SQ=" + ",".join(roi_sq) + "\n")
print(json.dumps(plan, indent=1)); print("ONLY_ARMS:", sorted(names))
