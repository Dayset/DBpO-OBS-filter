"""Bitstream-level foveation: tiles and "server-side packet loss".

1. Tiles. The frame is cut into a 3x3 grid and every tile is encoded on its
   own (the same independence HEVC/AV1 motion-constrained tiles impose). We
   measure (a) the tiling overhead at equal quality and (b) the best a
   foveated tile allocation can do at the same total bitrate as a plain CBR
   stream: center tile gets most of the bits, the 8 edge tiles get few.

2. Slice dropping (the "broken in-between frames" idea). A normal CBR stream
   is encoded with horizontal slices; in every non-key frame the top and
   bottom slices are deleted from the bitstream before it reaches the viewer,
   like provoked packet loss. The decoder conceals the holes. We measure the
   bytes saved and what the viewer sees in the center and at the edges.

Usage: python3 research/foveation/tiles_and_slices.py [frames]
"""
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import foveation_bench as fb  # noqa: E402

H, W, FPS = fb.H, fb.W, 60
TMP = fb.TMP


def psnr(ref, test, mask=None):
    m = np.ones(ref[0].shape[:2], bool) if mask is None else mask
    mse = np.mean([np.mean((fb.luma(a)[m] - fb.luma(b)[m]) ** 2) for a, b in zip(ref, test)])
    return 10 * np.log10(1 / max(mse, 1e-12))


def masks():
    y, x = np.mgrid[0:H, 0:W]
    m = np.maximum(np.abs((x + 0.5) / W - 0.5), np.abs((y + 0.5) / H - 0.5))
    return {"fovea": m < 0.15, "outer": m >= 0.3, "all": None}


TX = [0, 320, 640, 960]
TY = [0, 180, 360, 540]


def tile(frames, i, j):
    return [np.ascontiguousarray(f[TY[j]:TY[j + 1], TX[i]:TX[i + 1]]) for f in frames]


def encode_tile(frames, crf=None, kbps=None):
    path = os.path.join(TMP, "t.264")  # raw H.264: no container overhead
    fb.encode(frames, FPS, crf if crf is not None else 0, path, cbr_kbps=kbps)
    bits = os.path.getsize(path) * 8
    h, w = frames[0].shape[:2]
    return bits, fb.decode(path, w, h)


def assemble(tiles):
    n = len(tiles[(0, 0)])
    out = [np.zeros((H, W, 3), np.float32) for _ in range(n)]
    for (i, j), fr in tiles.items():
        for k in range(n):
            out[k][TY[j]:TY[j + 1], TX[i]:TX[i + 1]] = fr[k]
    return out


def tiles_experiment(src, kbps, reg):
    secs = len(src) / FPS
    print(f"\n-- Tiles ({len(src)} frames, budget {kbps} kbps)")
    # (a) overhead at equal CRF
    for crf in (23, 30):
        full_bits, full_dec = encode_tile(src, crf=crf)
        tb, td = 0, {}
        for i in range(3):
            for j in range(3):
                b, d = encode_tile(tile(src, i, j), crf=crf)
                tb += b
                td[(i, j)] = d
        print(f"   CRF {crf}: full frame {full_bits / secs / 1000:7.1f} kbps {psnr(src, full_dec):.2f} dB | "
              f"3x3 tiles {tb / secs / 1000:7.1f} kbps {psnr(src, assemble(td)):.2f} dB "
              f"(overhead {100 * (tb / full_bits - 1):+.1f}%)", flush=True)

    # (b) foveated allocation at the same total bits as plain CBR
    base_bits, base_dec = encode_tile(src, kbps=kbps)
    print(f"   plain CBR {kbps} kbps: fovea {psnr(src, base_dec, reg['fovea']):.2f} dB, "
          f"outer {psnr(src, base_dec, reg['outer']):.2f} dB, real {base_bits / secs / 1000:.0f} kbps")
    edge = [(i, j) for i in range(3) for j in range(3) if (i, j) != (1, 1)]
    for edge_crf in (28, 34, 40, 46, 51):
        eb, td = 0, {}
        for ij in edge:
            b, d = encode_tile(tile(src, *ij), crf=edge_crf)
            eb += b
            td[ij] = d
        left_kbps = (base_bits - eb) / secs / 1000
        if left_kbps < 20:
            print(f"   edges CRF {edge_crf}: edges alone use {eb / secs / 1000:.0f} kbps, nothing left for the center")
            continue
        cb, cd = encode_tile(tile(src, 1, 1), kbps=int(left_kbps))
        td[(1, 1)] = cd
        out = assemble(td)
        print(f"   edges CRF {edge_crf} ({eb / secs / 1000:5.0f} kbps) + center CBR {left_kbps:5.0f} kbps: "
              f"fovea {psnr(src, out, reg['fovea']):.2f} dB, outer {psnr(src, out, reg['outer']):.2f} dB, "
              f"total {(eb + cb) / secs / 1000:.0f} kbps", flush=True)


# ---- slice dropping -------------------------------------------------------

def nal_units(data):
    """Split Annex B into (start, end) of each NAL payload (after the start code)."""
    idx, i, n = [], 0, len(data)
    while True:
        i = data.find(b"\x00\x00\x01", i)
        if i < 0:
            break
        idx.append(i + 3)
        i += 3
    out = []
    for k, s in enumerate(idx):
        e = idx[k + 1] - 3 if k + 1 < len(idx) else n
        while e > s and data[e - 1] == 0:  # 4-byte start codes / trailing zeros
            e -= 1
        out.append((s, e))
    return out


def read_ue(buf, bitpos):
    zeros = 0
    while not (buf[bitpos // 8] >> (7 - bitpos % 8)) & 1:
        zeros += 1
        bitpos += 1
    bitpos += 1
    v = 0
    for _ in range(zeros):
        v = (v << 1) | ((buf[bitpos // 8] >> (7 - bitpos % 8)) & 1)
        bitpos += 1
    return (1 << zeros) - 1 + v, bitpos


def slices_experiment(src, kbps, reg, n_slices=6, drop=(0, -1)):
    secs = len(src) / FPS
    print(f"\n-- Slice dropping ({n_slices} horizontal slices, CBR {kbps} kbps, "
          f"drop slices {drop} in every non-key frame)")
    raw = os.path.join(TMP, "s.264")
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k",
           "-bufsize", f"{kbps}k", "-x264-params", f"nal-hrd=cbr:slices={n_slices}", "-pix_fmt", "yuv420p",
           "-g", str(2 * FPS), "-f", "h264", raw]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in src:
        p.stdin.write((np.clip(f, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())
    p.stdin.close()
    p.wait()
    data = open(raw, "rb").read()
    mbs_w = (W + 15) // 16
    nals = [(st, e, data[st] & 0x1F) for st, e in nal_units(data)]
    # Slice index within its frame: a frame starts at first_mb_in_slice == 0.
    first = {}
    for st, e, typ in nals:
        if typ in (1, 5):
            first[st] = read_ue(data[st + 1:st + 4], 0)[0]
    order, frame = {}, []
    for st, e, typ in nals + [(None, None, None)]:
        if st is None or (st in first and first[st] == 0 and frame):
            for rank, k in enumerate(sorted(frame, key=lambda k: first[k])):
                order[k] = rank
            frame = []
        if st in first:
            frame.append(st)
    drop_idx = {d % n_slices for d in drop}
    kept, dropped, band_rows = bytearray(), 0, None
    for st, e, typ in nals:
        if typ == 1 and order[st] in drop_idx:  # non-IDR slice at the top/bottom
            dropped += e - st + 4
            continue
        kept += b"\x00\x00\x00\x01" + data[st:e]
    starts = sorted({first[k] // mbs_w for k in first})
    top_rows, bottom_row = starts[1] * 16, starts[-1] * 16
    cut = os.path.join(TMP, "cut.264")
    open(cut, "wb").write(bytes(kept))
    full = fb.decode(raw, W, H)
    broken_raw = subprocess.run(["ffmpeg", "-v", "quiet", "-err_detect", "ignore_err", "-ec", "favor_inter+deblock",
                                 "-f", "h264", "-i", cut, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                capture_output=True).stdout
    broken = list(np.frombuffer(broken_raw, np.uint8).reshape(-1, H, W, 3).astype(np.float32) / 255)
    n = min(len(full), len(broken), len(src))
    print(f"   stream {len(data) * 8 / secs / 1000:.0f} kbps -> after dropping {len(kept) * 8 / secs / 1000:.0f} kbps "
          f"({100 * dropped / len(data):.0f}% of bytes removed); decoded {len(broken)}/{len(src)} frames")
    y = np.arange(H)[:, None] * np.ones((1, W), bool)
    kept_area = (y >= top_rows) & (y < bottom_row)
    for name, m in (("fovea", reg["fovea"]), ("kept bands", kept_area), ("dropped bands", ~kept_area)):
        a, b = psnr(src[:n], full[:n], m), psnr(src[:n], broken[:n], m)
        print(f"   {name:<14} intact {a:6.2f} dB   with dropped slices {b:6.2f} dB  ({b - a:+.2f})")
    # Drift over time in the kept area (errors leaking in through motion vectors).
    per = [psnr([src[k]], [broken[k]], kept_area) - psnr([src[k]], [full[k]], kept_area) for k in range(n)]
    q = n // 4
    print("   kept-area loss vs intact by quarter of the GOP: " +
          "  ".join(f"{np.mean(per[i * q:(i + 1) * q]):+.2f} dB" for i in range(4)))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    budgets = [int(v) for v in sys.argv[2].split(",")] if len(sys.argv) > 2 else [250, 1500]
    reg = masks()
    for clip, kbps in [(c, b) for b in budgets for c in ("fps_pan_clean", "fps_pan_grain")]:
        src = list(fb.h.clip_frames(clip, n))
        print(f"\n===== {clip} @ {kbps} kbps")
        tiles_experiment(src, kbps, reg)
        slices_experiment(src, kbps, reg)


if __name__ == "__main__":
    main()
