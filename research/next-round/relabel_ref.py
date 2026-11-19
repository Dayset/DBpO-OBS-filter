"""One-off: rows labelled clip "ref" came from the first out\\*\\ref.mkv in the run, clip Planetside_2_2026_11_15___21_29_34_08.
  python relabel_ref.py c:\\dbpo-lab\\t2\\results.jsonl     (makes results.jsonl.bak first)"""
import json, shutil, sys
p = sys.argv[1]; shutil.copy(p, p + ".bak"); n = 0; out = []
for l in open(p):
    r = json.loads(l)
    if r["clip"] == "ref": r["clip"] = "Planetside_2_2026_11_15___21_29_34_08"; n += 1
    out.append(json.dumps(r))
open(p, "w").write("\n".join(out) + "\n"); print("relabelled", n, "rows")
