"""Summarise grid_t2 results. Per clip, codec and rate:
 - cells whose measured rate is more than 10% off the target for any arm are excluded and counted (rate-control floors);
 - plain, best downscale, and the method settings on the trade-off curve (not beaten by another method setting on both
   the centre-50% VMAF and the whole-frame VMAF), next to the best downscale;
 - gate G2 (target <= 1500: some method setting beats the best downscale by 5 VMAF in the 50% box).
  python t2_summary.py OUT_DIR/results.jsonl
"""
import json, sys, collections
rows = [json.loads(l) for l in open(sys.argv[1])]
g = collections.defaultdict(list)
for r in rows:
    if not r.get("infeasible"): g[(r["clip"], r["codec"], r["kbps_target"])].append(r)
v = lambda r, k: r["vmaf"].get(k, float("nan"))
f = lambda r: f"{r['arm']:<16}{r['kbps']:>6.0f}k  centre30={v(r,'0.3_vmaf'):5.1f}  centre50={v(r,'0.5_vmaf'):5.1f}  whole={v(r,'1.0_vmaf'):5.1f}" if r else "-"
for key in sorted(g):
    allr = g[key]; ok = [r for r in allr if abs(r["kbps"] - key[2]) <= 0.1 * key[2]]
    print(key, f"({len(allr) - len(ok)} of {len(allr)} arms excluded: rate off target)" if len(ok) < len(allr) else "")
    plain = [r for r in ok if r["arm"] == "plain"]
    sc = [r for r in ok if r["arm"].startswith("scale")]; sq = [r for r in ok if r["arm"].startswith("sq") and "_roi" not in r["arm"]]
    roi = [r for r in ok if r["arm"].startswith(("roi_", "sroi_"))]; combo = [r for r in ok if r["arm"].startswith("sq") and "_roi" in r["arm"]]
    if not sq or not sc: print("  not enough matched arms\n"); continue
    bs = max(sc, key=lambda r: v(r, "0.5_vmaf"))
    print("  plain      ", f(plain[0] if plain else None)); print("  downscale  ", f(bs))
    def fr(S): return [a for a in S if not any(v(b, "0.5_vmaf") >= v(a, "0.5_vmaf") and v(b, "1.0_vmaf") >= v(a, "1.0_vmaf") and b is not a and (v(b, "0.5_vmaf") > v(a, "0.5_vmaf") or v(b, "1.0_vmaf") > v(a, "1.0_vmaf")) for b in S)]
    front = fr(sq)
    for a in sorted(front, key=lambda r: v(r, "1.0_vmaf")):
        print("  method     ", f(a), f"  vs downscale: centre50 {v(a,'0.5_vmaf') - v(bs,'0.5_vmaf'):+.1f}  whole {v(a,'1.0_vmaf') - v(bs,'1.0_vmaf'):+.1f}")
    if roi:
        for a in sorted(fr(roi), key=lambda r: v(r, "1.0_vmaf")): print("  ROI        ", f(a), f"  vs downscale: centre50 {v(a,'0.5_vmaf') - v(bs,'0.5_vmaf'):+.1f}  whole {v(a,'1.0_vmaf') - v(bs,'1.0_vmaf'):+.1f}")
        for a in sorted(fr(combo), key=lambda r: v(r, "1.0_vmaf")): print("  method+ROI ", f(a))
        for a in sorted(front, key=lambda r: v(r, "1.0_vmaf")):  # head to head: best ROI arm whose whole-frame VMAF is within 1 of this method arm
            m = [b for b in roi if v(b, "1.0_vmaf") >= v(a, "1.0_vmaf") - 1]
            print("  T12", a["arm"], "vs best ROI at matched whole frame:", f"{b['arm']} centre50 gap {v(a,'0.5_vmaf') - v(b,'0.5_vmaf'):+.1f}" if m and (b := max(m, key=lambda r: v(r, "0.5_vmaf"))) else "no ROI arm comes within 1 whole-frame VMAF (method ahead on the trade-off)")
    best = max(sq, key=lambda r: v(r, "0.5_vmaf"))
    if key[2] <= 1500: print("  G2:", "PASS" if v(best, "0.5_vmaf") - v(bs, "0.5_vmaf") >= 5 else "fail", f"(best centre50 gap {v(best,'0.5_vmaf') - v(bs,'0.5_vmaf'):+.1f})")
    print()
