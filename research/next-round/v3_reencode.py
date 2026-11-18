"""V3: does the method survive a platform transcode ladder?
Upload: the arm's canvas is encoded once at UPLOAD kbit/s (what the streamer sends). Platform: that upload is decoded and
re-encoded to each rung (scaled). Viewer: the rung is scaled back to the canvas, unsquished (method) or upscaled, scored
against the 1080p reference with the same boxes as grid_t2. One resumable run, one line per cell in OUT/results.jsonl.

  python v3_reencode.py OUT_DIR REF1.mkv [REF2.mkv ...]

Environment (optional):
  ARMS        plain,scale1280x720,sq1280x720_c30,scale960x540,sq960x540_c30
  UPLOAD      6000                       streamer upload rate, kbit/s
  UP_CODEC    x264                       upload encoder (stack_bench name, e.g. h264_nvenc)
  TRANS_CODEC x264                       platform transcoder
  RUNGS       source,852x480:1500,640x360:700,284x160:230   rung size:kbit/s ("source" = the upload itself)
Rungs are encoded at 60 fps so frames pair by index (real ladders often drop the low rungs to 30 fps; not modelled).
"""
import json, os, sys
import numpy as np, cv2
import grid_t2 as g

env = g.env
ARMS = env("ARMS", "plain,scale1280x720,sq1280x720_c30,scale960x540,sq960x540_c30").split(",")
UP = int(env("UPLOAD", "6000"))
UPC, TRC = env("UP_CODEC", "x264"), env("TRANS_CODEC", "x264")
RUNGS = []
for r in env("RUNGS", "source,852x480:1500,640x360:700,284x160:230").split(","):
    if r == "source": RUNGS.append(("source", None, UP))
    else:
        s, k = r.split(":"); RUNGS.append((s, tuple(int(v) for v in s.split("x")), int(k)))
W, H = g.W, g.H

def arm(name):
    if name == "plain": return (W, H), None
    sw, sh = (int(v) for v in name.split("_")[0].lstrip("scaleq").split("x"))
    if name.startswith("scale"): return (sw, sh), None
    ctr = int(name.split("_c")[1]) / 100
    p = g.pick_p(ctr / 2, sw / W)
    if p is None: return (sw, sh), "infeasible"
    return (sw, sh), g.SquishP(sw, sh, ctr / 2, p)

def main():
    work, refs = sys.argv[1], sys.argv[2:]
    os.makedirs(work, exist_ok=True)
    res = os.path.join(work, "results.jsonl"); done = set()
    if os.path.exists(res):
        for l in open(res): r = json.loads(l); done.add((r["clip"], r["arm"], r["rung"]))
    for ref in refs:
        clip = os.path.basename(os.path.dirname(os.path.abspath(ref))) if os.path.basename(ref).lower() == "ref.mkv" else os.path.splitext(os.path.basename(ref))[0]
        for name in ARMS:
            todo = [r for r in RUNGS if (clip, name, r[0]) not in done]
            if not todo: continue
            (cw, ch), sq = arm(name)
            if sq == "infeasible": print("infeasible", name); continue
            # canvas source (lossless), then the upload encode, decoded back to lossless = what the platform receives
            src = os.path.join(work, "_canvas.mkv"); w = g.sb.lossless_writer(cw, ch, src)
            for f in g.decode(ref, W, H):
                c = sq.fwd(f) if sq else (f if (cw, ch) == (W, H) else cv2.resize(f, (cw, ch), interpolation=cv2.INTER_AREA))
                w.stdin.write(np.ascontiguousarray(c).tobytes())
            w.stdin.close(); assert w.wait() == 0
            os.environ["CODEC"] = UPC; upl = os.path.join(work, "_upload.mkv"); g.sb.encode(src, upl, UP, None)
            ing = os.path.join(work, "_ingest.mkv"); w = g.sb.lossless_writer(cw, ch, ing)
            for f in g.decode(upl, cw, ch): w.stdin.write(f.tobytes())
            w.stdin.close(); assert w.wait() == 0
            def view(d, cw=cw, ch=ch, sq=sq):
                if d.shape[1] != cw or d.shape[0] != ch: d = cv2.resize(d, (cw, ch), interpolation=cv2.INTER_CUBIC)
                if sq: return sq.inv(d)
                return d if (cw, ch) == (W, H) else cv2.resize(d, (W, H), interpolation=cv2.INTER_CUBIC)
            for rung, size, kbps in todo:
                if rung == "source": rec = g.cell(ref, work, UPC, UP, src, None, view, (cw, ch), f"{name}_src")
                else: rec = g.cell(ref, work, TRC, kbps, ing, f"scale={size[0]}:{size[1]}:flags=bicubic", view, size, f"{name}_{rung}")
                out = {"clip": clip, "arm": name, "rung": rung, "upload_kbps": UP, "up_codec": UPC, "trans_codec": TRC, "kbps_target": kbps, **rec}
                open(res, "a").write(json.dumps(out) + "\n"); done.add((clip, name, rung))
                print(clip[-14:], name, rung, rec["kbps"], rec["vmaf"].get("0.5_vmaf"), rec["vmaf"].get("1.0_vmaf"), flush=True)
            for f in (src, upl, ing): os.remove(f)
    # summary: per clip and rung, centre-50 / whole VMAF per arm
    rows = [json.loads(l) for l in open(res)]
    for clip in sorted({r["clip"] for r in rows}):
        print(f"\n{clip}  (centre-50 / whole VMAF)")
        for rung, _, _ in RUNGS:
            cells = [r for r in rows if r["clip"] == clip and r["rung"] == rung]
            print(f"  {rung:>10}: " + "  ".join(f"{r['arm']} {r['vmaf'].get('0.5_vmaf')}/{r['vmaf'].get('1.0_vmaf')}" for r in cells))

main()
