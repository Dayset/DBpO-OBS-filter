"""Squish arm at 6000 kbit/s vs plain 6000, with the existing 1500-trained full-frame ternary model
and unsharp 0.25 on top. Same four test windows as real_restore.py.

  squish6000_eval.py prep  REF_RGB WORK   (encodes squish 6000 kbit/s, unsquish -> unsq6000.u8; plain6000.u8)
  squish6000_eval.py eval  REF_RGB WORK   (four windows, full-frame metrics, writes sq6000_eval.json)
"""
import json, os, sys
import numpy as np, cv2, torch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import real_restore as rr

N = 906
UNSHARP = 0.25

def prep(ref_path, work):
    rr.BUDGET = "6000k"
    ref = rr.mm(ref_path, (N, rr.H, rr.W, 3))
    if not os.path.exists(f"{work}/sq6000.done"):
        rr.stream_encode((np.clip(rr.squish(ref[i].astype(np.float32) / 255) * 255 + 0.5, 0, 255).astype(np.uint8) for i in range(N)),
                         rr.SQ_W, rr.SQ_H, f"{work}/sq6000.mkv")
        open(f"{work}/sq6000.done", "w").close()
    if not os.path.exists(f"{work}/unsq6000.done"):
        out = rr.mm(f"{work}/unsq6000.u8", (N, rr.H, rr.W, 3), "w+")
        for i, d in enumerate(rr.stream_decode(f"{work}/sq6000.mkv", rr.SQ_W, rr.SQ_H)):
            out[i] = np.clip(rr.unsquish(d.astype(np.float32) / 255) * 255 + 0.5, 0, 255).astype(np.uint8)
        out.flush(); del out
        open(f"{work}/unsq6000.done", "w").close()
    if not os.path.exists(f"{work}/plain6000.u8"):
        pl = rr.mm(f"{work}/plain6000.u8", (N, rr.H, rr.W, 3), "w+")
        for i, d in enumerate(rr.stream_decode(f"{work}/plain6000.mkv", rr.W, rr.H)):
            pl[i] = d
        pl.flush(); del pl
    print("prep6000 done", flush=True)

def eval6000(ref_path, work):
    torch.set_num_threads(2)
    ref = rr.mm(ref_path, (N, rr.H, rr.W, 3))
    unsq = rr.mm(f"{work}/unsq6000.u8", (N, rr.H, rr.W, 3)); plain = rr.mm(f"{work}/plain6000.u8", (N, rr.H, rr.W, 3))
    net = rr.Restorer(); net.load_state_dict(torch.load(f"{work}/restorer.pt", weights_only=False)["net"]); net.eval()
    starts, _ = rr.windows(work)
    res = []
    for s in starts:
        e = s + rr.WIN
        gt = rr.luma(ref[s:e].astype(np.float32) / 255)
        sq = unsq[s:e].astype(np.float32) / 255
        with torch.no_grad():
            cnn = np.concatenate([net(torch.from_numpy(sq[i:i + 8]).permute(0, 3, 1, 2)).permute(0, 2, 3, 1).numpy()
                                  for i in range(0, rr.WIN, 8)])
        cnn = np.clip(cnn, 0, 1)
        cnnu = np.clip(cnn + UNSHARP * (cnn - np.stack([cv2.GaussianBlur(f, (0, 0), 1.5) for f in cnn])), 0, 1)
        arms = {
            "plain_6000k": rr.luma(plain[s:e].astype(np.float32) / 255),
            "sq6000_bilinear": rr.luma(sq),
            "sq6000_ternary": rr.luma(cnn),
            "sq6000_ternary_unsharp0.25": rr.luma(cnnu),
        }
        row = {"start_s": round(s / rr.FPS, 2), "arms": {}}
        for k, v in arms.items():
            row["arms"][k] = rr.metric(v, gt)
        res.append(row)
        print(json.dumps(row), flush=True)
    json.dump({"windows": res, "note": "squish 6000 kbit/s; ternary model trained on 1500 kbit/s squish output"},
              open(f"{work}/sq6000_eval.json", "w"), indent=1)

if __name__ == "__main__":
    cmd, ref_path, work = sys.argv[1], sys.argv[2], sys.argv[3]
    if cmd == "prep": prep(ref_path, work)
    elif cmd == "eval": eval6000(ref_path, work)
