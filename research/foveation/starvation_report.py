"""Tables + a crop strip from starvation_bench.py results.

    python3 starvation_report.py WORK [frame]   # writes WORK/report.md and WORK/crops_<frame>.png
"""
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import starvation_bench as sb  # noqa: E402


def table(res, kbps, names, segs=("calm1", "spin", "calm2")):
    head = "| Variant | " + " | ".join(f"{s}: PSNR / center / SSIM / center / Mbit/s" for s in segs) + " | marker ok |"
    out = [head, "|" + "---|" * (len(segs) + 2)]
    for n in names:
        r = res.get(f"{n}@{kbps}")
        if not r:
            continue
        cells = [f"{r[s]['psnr']:.2f} / {r[s]['psnr_c']:.2f} / {r[s]['ssim']:.3f} / {r[s]['ssim_c']:.3f} / "
                 f"{r[s]['mbit_s']:.2f}" for s in segs]
        mk = r["all"].get("marker_ok")
        out.append(f"| {n} | " + " | ".join(cells) + f" | {'' if mk is None else f'{mk * 100:.1f}%'} |")
    return "\n".join(out)


def crops(work, kbps, names, frame):
    ref = sb.ref_frames(work)
    y0, x0, ch, cw = 360, 640, 360, 640
    tiles = [("reference", ref[frame][y0:y0 + ch, x0:x0 + cw])]
    for n in names:
        path = os.path.join(work, f"{n}_{kbps}.mp4")
        if not os.path.exists(path):
            continue
        v = sb.parse(n)
        w, h = sb.stream_size(v)
        for i, g in enumerate(sb.decode(path, w, h)):
            if i == frame:
                tiles.append((f"{n} @ {kbps}", sb.viewer(g, v, v["h"])[y0:y0 + ch, x0:x0 + cw]))
                break
    rows = []
    for k in range(0, len(tiles), 2):
        pair = tiles[k:k + 2] + ([("", np.zeros((ch, cw, 3), np.uint8))] if len(tiles[k:k + 2]) < 2 else [])
        row = []
        for label, t in pair:
            t = np.ascontiguousarray(t).copy()
            cv2.putText(t, label, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
            row.append(t)
        rows.append(np.concatenate(row, 1))
    out = os.path.join(work, f"crops_{frame}_{kbps}.png")
    cv2.imwrite(out, cv2.cvtColor(np.concatenate(rows, 0), cv2.COLOR_RGB2BGR))
    return out


def main():
    work = sys.argv[1]
    frame = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    res = json.load(open(os.path.join(work, "results.json")))
    md = []
    for kbps in (6000, 3000):
        names = [k.split("@")[0] for k in res if k.endswith(f"@{kbps}")]
        md += [f"## {kbps} kbit/s", "", table(res, kbps, names), ""]
    if "detect" in res:
        md += ["## Detector", "", "```", json.dumps(res["detect"], indent=1), "```"]
    open(os.path.join(work, "report.md"), "w").write("\n".join(md))
    for kbps in (6000, 3000):
        print(crops(work, kbps, ["base", "720_black", "540_black", "360_black", "540_edge", "sq810"], frame))


if __name__ == "__main__":
    main()
