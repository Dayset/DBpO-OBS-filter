"""Bitrate x picture size x squeeze sweep (issue #18, epic #10). Runs unattended and resumes at any moment.

    python dbpo_sweep.py auto  [--quick | --full | --stage2]   # check -> plan -> run -> report (what run_sweep.bat calls)
    python dbpo_sweep.py check  --sources DIR                  # ffmpeg, encoders, golden self-test, disk space, sources
    python dbpo_sweep.py plan   --sources DIR --out DIR [--quick|--full|--stage2] [--a 0:63:1] [--q 0:7]
                                [--bitrate 500,1000] [--encoder x264,nvenc] [--preset veryfast,medium] ...
    python dbpo_sweep.py run    --out DIR [--jobs N]           # executes pending runs; Ctrl+C any time, rerun to resume
    python dbpo_sweep.py report --out DIR                      # best.csv + summary.md
    python dbpo_sweep.py status --out DIR

Sources: DIR/<class>/<clip>.<ext>, the folder name is the source class (busy_action, fast_pan, static_ui,
text_scroll, talking_head, mixed_hud, or any name you like). 1920x1080 at 60 fps from a lossless or >= 50 Mbit/s
master; other sizes / rates are converted (lanczos, fps filter) and flagged in the manifest.

Stream model (same as seg_bench.py / starvation_math.render2): 1920x1080 frame, picture top-left at scale step a,
black elsewhere, marker v2 last. Squeeze q uses the squish mapping with ratio r*g, g = f^(q/7), on the base config
--squish-cfg (default rx=ry=1: q=0 is the even shrink, q>0 moves the shrink to the edges). q is clamped per a to
what the density floor allows (starvation_math.max_q); the row stores the effective q and the requested q_req.
Viewer = crop + bilinear unsquish (starvation_math.view2), luma only.

Interlace step (issue #19, --pack): at full size (a=0) the picture is sent one field per frame. "field" packs the
field into the top half (black below); "split" keeps even fields in the top half and odd fields in the bottom half,
each frame replacing only its own half. Viewer model: ffmpeg bwdif on the received fields (crop -> weave -> bwdif),
the modern motion-adaptive deinterlacer the extension is measured against. No marker in these runs (marker_pct empty).

Durability: every finished run is appended to results.csv (and its frames to frames.csv), flushed and fsynced
before the next result is handled. A rerun skips every run_id with status ok; failed runs are retried
(--skip-failed to keep them). Nothing else holds state.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import multiprocessing as mp
import os
import platform
import random
import shutil
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "vendor")
sys.path.insert(0, VENDOR)

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from scipy import sparse  # noqa: E402

import atlas_math as am  # noqa: E402
import fieldpack_math as fm  # noqa: E402
import starvation_math as sm  # noqa: E402

SCRIPT_VERSION = "1.1.0"
W, H, FPS = 1920, 1080, 60
SAMPLE_STEP, RAND_SEED, RAND_N = 4, 7, 3
BASE_CFG = "FSQ1:rx=1,ry=1,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778"
FLOOR = 0.08
VIDEO_EXT = {".mkv", ".mp4", ".mov", ".avi", ".webm", ".flv", ".ts", ".m2ts", ".nut", ".y4m"}
CLASSES = ["busy_action", "fast_pan", "static_ui", "text_scroll", "talking_head", "mixed_hud"]

SCHEMA = ["source", "source_class", "temporal_complexity", "spatial_complexity", "encoder", "bitrate_kbps", "a",
          "picture_h", "q", "centre_ssim", "centre_psnr", "edge_ssim", "edge_psnr", "whole_ssim", "whole_psnr",
          "marker_pct", "mbit_s", "encode_s", "frames_sampled", "ffmpeg_version", "script_version", "source_sha256"]
EXTRA = ["q_req", "excerpt_start_s", "excerpt_s", "rand_centre_ssim", "rand_whole_ssim", "runtime_s", "status",
         "error", "run_id", "finished_at", "pack"]
COLUMNS = SCHEMA + EXTRA
FRAME_COLS = ["run_id", "frame", "kind", "centre_ssim", "centre_psnr", "edge_ssim", "edge_psnr", "whole_ssim",
              "whole_psnr", "marker_ok"]

PACKS = ["field", "split"]  # interlace step variants (a = 0 only)
PLANS = {  # (excerpt s, bitrates, a, q, encoders, interlace packs)
    "quick": (10, [1000, 6000], [0, 38, 63], [0, 7], ["x264:veryfast"], PACKS),
    "quick4": (4, [3000, 6000], [0, 16, 25, 38, 50], [0, 7], ["x264:veryfast", "nvenc"], PACKS),
    "full": (10, [1000, 2000, 3000, 6000], [0, 25, 38, 50, 63], [0, 7], ["x264:veryfast"], PACKS),
}

# metric regions (luma). The marker strip (bottom right, 160x32) is excluded everywhere: it is not picture.
_cy0, _cy1, _cx0, _cx1 = int(H * .35), int(H * .65), int(W * .35), int(W * .65)
_ey0, _ey1, _ex0, _ex1 = int(H * .2), int(H * .8), int(W * .2), int(W * .8)
_MX, _MY = sm.ext_pos(W, H)
M_CENTRE = np.zeros((H, W), bool)
M_CENTRE[_cy0:_cy1, _cx0:_cx1] = True
M_EDGE = np.ones((H, W), bool)
M_EDGE[_ey0:_ey1, _ex0:_ex1] = False
M_WHOLE = np.ones((H, W), bool)
for _m in (M_EDGE, M_WHOLE):
    _m[_MY:_MY + 32, _MX:_MX + 160] = False


# ---------------------------------------------------------------- small helpers

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def ffbin(name):
    env = os.environ.get(name.upper())
    return env or shutil.which(name) or name


def parse_list(spec, cast=int):
    """'0:63:1' (inclusive range), '0,16,25', or mixed '0,10:20:5'."""
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            bits = part.split(":")
            lo, hi = cast(bits[0]), cast(bits[1])
            st = cast(bits[2]) if len(bits) > 2 else cast(1)
            v = lo
            while v <= hi + 1e-9:
                out.append(v)
                v += st
        else:
            out.append(cast(part))
    seen, res = set(), []
    for v in out:
        if v not in seen:
            seen.add(v)
            res.append(v)
    return res


def sha256(path, cache):
    st = os.stat(path)
    key = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    if key in cache:
        return cache[key]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    cache[key] = h.hexdigest()
    return cache[key]


def file_sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def ffmpeg_version():
    try:
        line = subprocess.run([ffbin("ffmpeg"), "-version"], capture_output=True, text=True).stdout.splitlines()[0]
        return line.split(" Copyright")[0].replace(",", ";")
    except (OSError, IndexError):
        return "missing"


def probe(path):
    r = subprocess.run([ffbin("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate,codec_name,pix_fmt,bit_rate:format=duration,bit_rate",
                        "-of", "json", path], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"ffprobe failed on {path}: {r.stderr.strip()[:300]}")
    j = json.loads(r.stdout)
    s, fm = j["streams"][0], j.get("format", {})
    num, den = (s.get("r_frame_rate") or "0/1").split("/")
    br = s.get("bit_rate") or fm.get("bit_rate") or 0
    return {"width": int(s["width"]), "height": int(s["height"]), "fps": float(num) / max(float(den), 1),
            "codec": s.get("codec_name"), "pix_fmt": s.get("pix_fmt"), "duration": float(fm.get("duration") or 0),
            "mbit_s": round(float(br) / 1e6, 1)}


def keep_awake(on=True):
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if on else 0))


def lower_priority():
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
        else:
            os.nice(10)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- source frames

def src_cmd(path, start, n, fmt="rgb24"):
    """Decoded excerpt at 1920x1080@60 as raw frames on stdout. fmt='gray' gives the reference luma exactly as the
    encoder sees it: RGB -> BT.709 limited YUV -> full-range gray (the same path the encoded stream is read back with)."""
    vf = f"scale={W}:{H}:flags=lanczos,fps={FPS},format=rgb24"
    if fmt == "gray":
        vf += (",scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
               "scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=gray")
    return [ffbin("ffmpeg"), "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-i", path, "-vf", vf,
            "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", fmt, "-"]


def read_frames(proc, shape):
    n = int(np.prod(shape))
    while True:
        b = proc.stdout.read(n)
        if not b or len(b) < n:
            return
        yield np.frombuffer(b, np.uint8).reshape(shape)


def sample_indices(n):
    step = list(range(0, n, SAMPLE_STEP))
    rest = [i for i in range(n) if i % SAMPLE_STEP]
    rnd = sorted(random.Random(RAND_SEED).sample(rest, min(RAND_N, len(rest))))
    return step, rnd


def busiest_start(path, dur, excerpt):
    """Start (s) of the excerpt with the highest mean temporal metric, on a 240x135 pass of the whole file."""
    cmd = [ffbin("ffmpeg"), "-v", "error", "-nostdin", "-i", path, "-vf", f"fps={FPS},scale=240:135:flags=area,format=rgb24",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=1 << 20)
    prev, t = None, []
    for f in read_frames(p, (135, 240, 3)):
        m = sm.metric_frame(f)
        t.append(0.0 if prev is None else sm.temporal(prev, m))
        prev = m
    p.wait()
    win = int(round(excerpt * FPS))
    if len(t) <= win:
        return 0.0
    c = np.concatenate([[0], np.cumsum(t)])
    sums = c[win:] - c[:-win]
    return round(int(np.argmax(sums[::FPS // 2])) * (FPS // 2) / FPS, 3)  # half-second grid


# ---------------------------------------------------------------- sources + plan

def discover(src_dir):
    out = []
    lst = os.path.join(src_dir, "sources.csv")
    if os.path.exists(lst):  # optional: path,class rows
        for row in csv.reader(open(lst, encoding="utf-8")):
            if row and not row[0].startswith("#") and row[0] != "path":
                p = row[0] if os.path.isabs(row[0]) else os.path.join(src_dir, row[0])
                out.append((os.path.abspath(p), row[1].strip() if len(row) > 1 else "unknown"))
        return out
    for cls in sorted(os.listdir(src_dir)):
        d = os.path.join(src_dir, cls)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if os.path.splitext(fn)[1].lower() in VIDEO_EXT:
                out.append((os.path.abspath(os.path.join(d, fn)), cls))
    return out


def scan_sources(src_dir, out_dir, excerpt, where, only=None):
    """Probe + hash every source and pick its excerpt. Cached in OUT/sources.json."""
    db = load_json(os.path.join(out_dir, "sources.json"), {"hash_cache": {}, "sources": {}})
    found = discover(src_dir)
    if only:
        found = [x for x in found if any(o.lower() in os.path.basename(x[0]).lower() for o in only)]
    if not found:
        raise SystemExit(f"No sources in {src_dir}. Put clips in {src_dir}/<class>/clip.mkv (see README.md).")
    res = []
    for path, cls in found:
        name = os.path.splitext(os.path.basename(path))[0]
        log(f"source {cls}/{name}: probing + hashing")
        info = probe(path)
        sha = sha256(path, db["hash_cache"])
        ex = min(excerpt, info["duration"]) if excerpt else info["duration"]
        ex = math.floor(ex * FPS) / FPS
        key = f"{sha}|{ex}|{where}"
        cached = db["sources"].get(key)
        if cached:
            res.append(cached)
            continue
        if where == "busiest":
            start = busiest_start(path, info["duration"], ex)
        elif where == "middle":
            start = max(0.0, round((info["duration"] - ex) / 2, 3))
        elif where == "start":
            start = 0.0
        else:
            start = float(where)
        warn = []
        if (info["width"], info["height"]) != (W, H):
            warn.append(f"size {info['width']}x{info['height']} converted to {W}x{H}")
        if abs(info["fps"] - FPS) > 0.05:
            warn.append(f"{info['fps']:.2f} fps converted to {FPS}")
        lossless = info["codec"] in ("ffv1", "utvideo", "huffyuv", "rawvideo", "magicyuv", "prores", "dnxhd", "qtrle", "png")
        if not lossless and info["mbit_s"] and info["mbit_s"] < 50:
            warn.append(f"lossy {info['codec']} at {info['mbit_s']} Mbit/s (< 50, spec wants lossless or >= 50)")
        rec = {"name": name, "class": cls, "path": path, "sha256": sha, "probe": info, "excerpt_start_s": start,
               "excerpt_s": ex, "where": where, "warnings": warn, "key": key}
        db["sources"][key] = rec
        save_json(os.path.join(out_dir, "sources.json"), db)
        res.append(rec)
        for w_ in warn:
            log(f"  WARNING {name}: {w_}")
    save_json(os.path.join(out_dir, "sources.json"), db)
    return res


def enc_list(encoders, presets):
    out = []
    for e in encoders:
        if ":" in e:
            out.append(tuple(e.split(":", 1)))
        elif e == "x264":
            out += [("x264", p) for p in presets]
        elif e == "nvenc":
            out.append(("nvenc", "p7"))
        else:
            raise SystemExit(f"unknown encoder {e} (x264, nvenc, x264:<preset>, nvenc:<p1..p7>)")
    return out


def q_effective(a, q, cfg, floor):
    if a == 0:
        return 0  # full size: the squeeze has nothing to move
    return min(q, sm.max_q(a, cfg, floor))


def run_id(src, enc, preset, kb, a, q, cfg, floor, pack=""):
    s = f"{src['sha256']}|{src['excerpt_start_s']}|{src['excerpt_s']}|{enc}|{preset}|{kb}|{a}|{q}|{cfg}|{floor}|v1"
    if pack:
        s += f"|pack={pack}"
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def make_runs(sources, bitrates, a_list, q_list, encs, cfg, floor, packs=()):
    runs = []
    for s in sources:
        for enc, preset in encs:
            for kb in bitrates:
                seen = set()
                for a in a_list:
                    for q in q_list:
                        qe = q_effective(a, q, cfg, floor)
                        if (a, qe) in seen:
                            continue
                        seen.add((a, qe))
                        runs.append({"run_id": run_id(s, enc, preset, kb, a, qe, cfg, floor), "source_key": s["key"],
                                     "source": s["name"], "source_class": s["class"], "encoder": enc, "preset": preset,
                                     "bitrate_kbps": kb, "a": a, "q": qe, "q_req": q, "cfg": cfg, "floor": floor})
                for pk in packs:
                    runs.append({"run_id": run_id(s, enc, preset, kb, 0, 0, cfg, floor, pk), "source_key": s["key"],
                                 "source": s["name"], "source_class": s["class"], "encoder": enc, "preset": preset,
                                 "bitrate_kbps": kb, "a": 0, "q": 0, "q_req": 0, "cfg": cfg, "floor": floor, "pack": pk})
    return runs


def stage2_runs(out_dir, sources_db, cfg, floor):
    """Best 2 sizes per source x bitrate from the 10 s x264 runs, full length, q in {0,2,4,7}; plus NVENC P7 on the
    stage-1 size grid (q=0, same excerpts) for H4."""
    rows = [r for r in load_results(out_dir).values() if r["status"] == "ok"]
    if not rows:
        raise SystemExit("--stage2 needs stage 1 results in this --out folder")
    by_key = {s["key"]: s for s in sources_db["sources"].values()}
    runs, groups = [], {}
    for r in rows:
        if r["encoder"].startswith("x264"):
            groups.setdefault((r["source_sha256"], r["excerpt_start_s"], r["excerpt_s"], r["encoder"], r["bitrate_kbps"]), []).append(r)
    for (sha, st, ex, enc, kb), rs in groups.items():
        src = next((s for s in by_key.values() if s["sha256"] == sha and str(s["excerpt_start_s"]) == st and str(s["excerpt_s"]) == ex), None)
        if src is None:
            continue
        best = {}
        for r in rs:
            a = int(r["a"])
            best[a] = max(best.get(a, -1), float(r["centre_ssim"]))
        top = [a for a, _ in sorted(best.items(), key=lambda x: -x[1]) if a][:2]
        long_src = dict(src)
        long_src["excerpt_s"] = math.floor(min(20, src["probe"]["duration"]) * FPS) / FPS
        long_src["excerpt_start_s"] = max(0.0, min(src["excerpt_start_s"], src["probe"]["duration"] - long_src["excerpt_s"]))
        long_src["key"] = f"{sha}|{long_src['excerpt_s']}|{long_src['excerpt_start_s']}|stage2"
        sources_db["sources"][long_src["key"]] = long_src
        e, p = enc.split("-", 1)
        runs += make_runs([long_src], [int(kb)], [0] + top, [0, 2, 4, 7], [(e, p)], cfg, floor)
        a_grid = sorted(best)
        runs += make_runs([src], [int(kb)], a_grid, [0], [("nvenc", "p7")], cfg, floor)
    save_json(os.path.join(out_dir, "sources.json"), sources_db)
    return runs


def cmd_plan(args, quiet=False):
    os.makedirs(args.out, exist_ok=True)
    plan_path = os.path.join(args.out, "plan.json")
    plan = load_json(plan_path, {"runs": [], "history": []})
    cfg, floor = args.squish_cfg, args.floor
    if args.stage2:
        db = load_json(os.path.join(args.out, "sources.json"), {"sources": {}})
        new = stage2_runs(args.out, db, cfg, floor)
        label = "stage2"
    else:
        name = "quick" if args.quick else "quick4" if args.quick4 else "full"
        ex, kbs, a_l, q_l, encs, packs = PLANS[name]
        if args.pack is not None:
            packs = [p for p in args.pack.split(",") if p and p != "none"]
            bad = [p for p in packs if p not in PACKS]
            if bad:
                raise SystemExit(f"unknown --pack {bad} (field, split, none)")
        if name == "quick4" and not args.encoder and not nvenc_available():
            encs = [e for e in encs if not e.startswith("nvenc")]
        ex = args.excerpt if args.excerpt else ex
        kbs = parse_list(args.bitrate) if args.bitrate else kbs
        a_l = parse_list(args.a) if args.a else a_l
        q_l = parse_list(args.q) if args.q else q_l
        encs = args.encoder.split(",") if args.encoder else encs
        presets = args.preset.split(",") if args.preset else ["veryfast"]
        for a in a_l:
            if not 0 <= a <= sm.A_MAX:
                raise SystemExit(f"a={a} outside 0..63")
        for q in q_l:
            if not 0 <= q <= sm.Q_MAX:
                raise SystemExit(f"q={q} outside 0..7")
        sources = scan_sources(args.sources, args.out, ex, args.excerpt_at, args.only)
        new = make_runs(sources, kbs, a_l, q_l, enc_list(encs, presets), cfg, floor, packs)
        label = name
    have = {r["run_id"] for r in plan["runs"]}
    added = [r for r in new if r["run_id"] not in have]
    plan["runs"] += added
    plan["history"].append({"at": dt.datetime.now().isoformat(timespec="seconds"), "plan": label, "added": len(added),
                            "argv": sys.argv[1:]})
    save_json(plan_path, plan)
    clamped = sorted({(r["a"], r["q_req"], r["q"]) for r in added if r["q"] != r["q_req"] and r["a"]})
    if not quiet or added:
        log(f"plan '{label}': {len(added)} new runs ({len(new) - len(added)} already planned), {len(plan['runs'])} in plan")
    if clamped:
        log("squeeze clamped by the density floor (a, q asked -> q used): " +
            ", ".join(f"({a},{q}->{e})" for a, q, e in clamped))
    write_manifest(args.out)
    return plan


# ---------------------------------------------------------------- reference cache (one pass per excerpt)

def ref_path(out_dir, src):
    return os.path.join(out_dir, "cache", f"{src['sha256'][:12]}_{src['excerpt_start_s']}_{src['excerpt_s']}.npy")


def prepare(out_dir, src):
    """Reference luma of the sampled frames (memmap .npy) + temporal/spatial complexity of the excerpt."""
    path = ref_path(out_dir, src)
    meta_path = path[:-4] + ".json"
    if os.path.exists(path) and os.path.exists(meta_path):
        return load_json(meta_path, None)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n = int(round(src["excerpt_s"] * FPS))
    step, rnd = sample_indices(n)
    want = sorted(set(step) | set(rnd))
    log(f"preparing reference for {src['class']}/{src['name']} ({n} frames, {len(want)} sampled)")
    tmp = path + ".part.npy"
    arr = np.lib.format.open_memmap(tmp, mode="w+", dtype=np.uint8, shape=(len(want), H, W))
    prgb = subprocess.Popen(src_cmd(src["path"], src["excerpt_start_s"], n), stdout=subprocess.PIPE, bufsize=1 << 24)
    pg = subprocess.Popen(src_cmd(src["path"], src["excerpt_start_s"], n, "gray"), stdout=subprocess.PIPE, bufsize=1 << 24)
    prev, temp, spat, k, got = None, [], [], 0, 0
    pos = {f: i for i, f in enumerate(want)}
    for i, (rgb, g) in enumerate(zip(read_frames(prgb, (H, W, 3)), read_frames(pg, (H, W)))):
        m = sm.metric_frame(rgb)
        if prev is not None:
            temp.append(sm.temporal(prev, m))
        prev = m
        if i in pos:
            arr[pos[i]] = g
            gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
            spat.append(float(np.sqrt(gx * gx + gy * gy).mean()))
            k += 1
        got = i + 1
    _kill(prgb, pg)
    arr.flush()
    del arr
    if got < n:
        os.remove(tmp)
        raise RuntimeError(f"{src['name']}: only {got} of {n} frames decoded (clip too short for the excerpt?)")
    os.replace(tmp, path)
    meta = {"frames": n, "sampled": want, "step": step, "random": rnd, "seed": RAND_SEED,
            "temporal_complexity": round(float(np.mean(temp)) if temp else 0.0, 4),
            "spatial_complexity": round(float(np.mean(spat)), 4)}
    save_json(meta_path, meta)
    return meta


# ---------------------------------------------------------------- one run (worker)

def ssim_map(a, b):
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    bf = lambda x: cv2.boxFilter(x, -1, (7, 7), borderType=cv2.BORDER_REFLECT)  # noqa: E731
    ma, mb = bf(a), bf(b)
    va, vb, cab = bf(a * a) - ma * ma, bf(b * b) - mb * mb, bf(a * b) - ma * mb
    return ((2 * ma * mb + c1) * (2 * cab + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))


def psnr(d2, mask):
    mse = float(d2[mask].mean())
    return 100.0 if mse <= 1e-10 else min(100.0, 10 * math.log10(255 * 255 / mse))


def frame_metrics(ref, got):
    s = ssim_map(ref, got)
    d = ref.astype(np.float32) - got.astype(np.float32)
    d2 = d * d
    return (float(s[M_CENTRE].mean()), psnr(d2, M_CENTRE), float(s[M_EDGE].mean()), psnr(d2, M_EDGE),
            float(s[M_WHOLE].mean()), psnr(d2, M_WHOLE))


class Geometry:
    """Sender + viewer resampling for one (a, q): the matrices of starvation_math.render2 / view2, as sparse/dense
    products so a 1080p frame costs milliseconds. Equal to render2 up to rare +-1 rounding (checked by `check`)."""

    def __init__(self, a, q, cfg):
        self.a, self.q = a, q
        self.rw, self.rh = sm.rect2(a, W, H)
        if a == 0:
            return
        ax, ay = sm._axes(cfg, sm.squeeze(a, q))
        self.ry = sparse.csr_matrix(am.area_matrix(ay, H, self.rh))
        self.rx = sparse.csr_matrix(am.area_matrix(ax, W, self.rw))
        self.vy = am.bilinear_matrix(ay, H, self.rh).astype(np.float32)
        self.vxT = am.bilinear_matrix(ax, W, self.rw).T.astype(np.float32).copy()

    def render(self, src):
        out = np.zeros_like(src) if self.a else src.copy()
        if self.a:
            t = self.ry @ src.reshape(H, W * 3).astype(np.float64)  # (rh, W*3)
            t = t.reshape(self.rh, W, 3).transpose(1, 0, 2).reshape(W, self.rh * 3)
            p = (self.rx @ t).reshape(self.rw, self.rh, 3).transpose(1, 0, 2)
            out[:self.rh, :self.rw] = np.clip(p + 0.5, 0, 255).astype(np.uint8)
        return sm.draw_marker2(out, self.a, self.q)

    def view(self, g):
        if not self.a:
            return g
        crop = g[:self.rh, :self.rw].astype(np.float32)
        return np.clip(self.vy @ crop @ self.vxT + 0.5, 0, 255).astype(np.uint8)


class PackGeometry:
    """Interlace step at full size: one field per frame (fieldpack_math). Stateful: call render() in frame order."""

    def __init__(self, pack):
        self.pack, self.prev, self.n = pack, None, 0

    def render(self, src):
        p = self.n & 1
        self.n += 1
        if self.pack == "split":
            self.prev = fm.pack_split(src, p, self.prev, 1)
            return self.prev
        return fm.pack(src, p, 1)

    def decode_vf(self):
        """ffmpeg filter: received fields -> 60 full frames per second through bwdif (one field of look-ahead)."""
        crop = f"crop={W}:{H // 2}:0:" + ("'if(mod(n,2),%d,0)'" % (H // 2) if self.pack == "split" else "0")
        return f"{crop},weave=first_field=top,setfield=tff,bwdif=mode=send_field:parity=tff:deint=all,"

    @staticmethod
    def view(g):
        return g


def nvenc_available():
    r = subprocess.run([ffbin("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=60", "-frames:v", "3",
                        "-c:v", "h264_nvenc", "-pix_fmt", "yuv420p", "-f", "null", "-"], capture_output=True)
    return r.returncode == 0


def enc_args(enc, preset, kb, threads, nvenc_extra):
    rate = ["-b:v", f"{kb}k", "-maxrate", f"{kb}k", "-bufsize", f"{kb}k"]
    common = ["-g", str(2 * FPS), "-keyint_min", str(2 * FPS), "-bf", "2", "-profile:v", "high"]
    if enc == "x264":
        return ["-c:v", "libx264", "-preset", preset, "-threads", str(threads),
                "-x264-params", "nal-hrd=cbr:force-cfr=1"] + common + rate
    if enc == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", preset, "-tune", "hq", "-rc", "cbr", "-multipass", "fullres"] + \
            common + rate + (nvenc_extra.split() if nvenc_extra else [])
    raise ValueError(enc)


_SEM = None


def _init_worker(sem, low):
    global _SEM
    _SEM = sem
    cv2.setNumThreads(2)
    if low:
        lower_priority()


def _kill(*procs):
    for p in procs:
        if p and p.poll() is None:
            try:
                p.kill()
                p.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass


def do_run(job):
    run, src, meta, opt = job["run"], job["src"], job["meta"], job["opt"]
    t0 = time.time()
    row = {c: "" for c in COLUMNS}
    row.update(source=run["source"], source_class=run["source_class"], encoder=f"{run['encoder']}-{run['preset']}",
               bitrate_kbps=run["bitrate_kbps"], a=run["a"], q=run["q"], q_req=run["q_req"],
               picture_h=sm.rect2(run["a"], W, H)[1], source_sha256=src["sha256"],
               temporal_complexity=meta["temporal_complexity"], spatial_complexity=meta["spatial_complexity"],
               ffmpeg_version=opt["ffmpeg_version"], script_version=SCRIPT_VERSION,
               excerpt_start_s=src["excerpt_start_s"], excerpt_s=src["excerpt_s"], run_id=run["run_id"],
               pack=run.get("pack", ""))
    work = os.path.join(opt["work"], run["run_id"])
    os.makedirs(work, exist_ok=True)
    stream = os.path.join(work, "stream.mkv")
    frames = []
    pdec = penc = pout = elog = None
    locked = False
    try:
        pack = run.get("pack", "")
        geo = PackGeometry(pack) if pack else Geometry(run["a"], run["q"], run["cfg"])
        n = meta["frames"]
        if run["encoder"] == "nvenc" and _SEM is not None:
            _SEM.acquire()
            locked = True
        te = time.time()
        pdec = subprocess.Popen(src_cmd(src["path"], src["excerpt_start_s"], n), stdout=subprocess.PIPE, bufsize=1 << 24)
        elog = open(os.path.join(work, "encode.log"), "wb")
        penc = subprocess.Popen([ffbin("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                 "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                                 "-vf", "scale=out_color_matrix=bt709:out_range=tv", "-pix_fmt", "yuv420p",
                                 "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
                                 "-color_trc", "bt709"] +
                                enc_args(run["encoder"], run["preset"], run["bitrate_kbps"], opt["x264_threads"],
                                         opt["nvenc_extra"]) + [stream],
                                stdin=subprocess.PIPE, stderr=elog)
        sent = 0
        try:
            for f in read_frames(pdec, (H, W, 3)):
                penc.stdin.write(geo.render(f).tobytes())
                sent += 1
            penc.stdin.close()
        except (BrokenPipeError, OSError):
            pass  # encoder died; its log explains below
        rc = penc.wait()
        elog.close()
        _kill(pdec)  # done or encoder died: never block on a decoder still writing into a full pipe
        if locked:
            _SEM.release()
            locked = False
        if rc or sent < n:
            err = open(os.path.join(work, "encode.log"), errors="replace").read().strip()[-400:]
            raise RuntimeError(f"encode failed (rc {rc}, {sent}/{n} frames): {err}")
        row["encode_s"] = round(time.time() - te, 2)
        pk = subprocess.run([ffbin("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=size",
                             "-of", "csv=p=0", stream], capture_output=True, text=True)
        total = sum(int(x) for x in pk.stdout.split() if x.strip().isdigit())
        row["mbit_s"] = round(total * 8 / (n / FPS) / 1e6, 4)

        ref = np.load(ref_path(opt["out"], src), mmap_mode="r")
        pos = {f: i for i, f in enumerate(meta["sampled"])}
        rnd = set(meta["random"])
        stp = set(meta["step"])
        pout = subprocess.Popen([ffbin("ffmpeg"), "-v", "error", "-nostdin", "-i", stream, "-vf",
                                 (geo.decode_vf() if pack else "") +
                                 "scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=gray",
                                 "-f", "rawvideo", "-pix_fmt", "gray", "-"], stdout=subprocess.PIPE, bufsize=1 << 24)
        got = 0
        for i, g in enumerate(read_frames(pout, (H, W))):
            got = i + 1
            if i not in pos:
                continue
            ok = True if pack else sm.read_state(g) == (run["a"], run["q"])
            m = frame_metrics(ref[pos[i]], geo.view(g))
            for kind in (["step"] if i in stp else []) + (["random"] if i in rnd else []):
                frames.append([run["run_id"], i, kind] + [round(x, 5) for x in m] + [int(ok)])
        pout.wait()
        if got < n - (1 if pack else 0):  # weave drops a last unpaired field
            raise RuntimeError(f"decoded {got}/{n} frames")
        st = [f for f in frames if f[2] == "step"]
        rd = [f for f in frames if f[2] == "random"]
        mean = lambda rows, k: round(float(np.mean([r[k] for r in rows])), 5) if rows else ""  # noqa: E731
        row.update(centre_ssim=mean(st, 3), centre_psnr=mean(st, 4), edge_ssim=mean(st, 5), edge_psnr=mean(st, 6),
                   whole_ssim=mean(st, 7), whole_psnr=mean(st, 8), frames_sampled=len(st),
                   marker_pct="" if pack else round(100.0 * sum(f[9] for f in frames) / max(len(frames), 1), 2),
                   rand_centre_ssim=mean(rd, 3), rand_whole_ssim=mean(rd, 7), status="ok")
    except Exception as e:  # noqa: BLE001
        row["status"] = "failed"
        row["error"] = (f"{type(e).__name__}: {e}".replace("\n", " ") + " | " +
                        traceback.format_exc().strip().splitlines()[-2][:200]).replace(",", ";")
        frames = []
    finally:
        if locked:
            _SEM.release()
        _kill(pdec, penc, pout)
        if elog and not elog.closed:
            elog.close()
        if not opt["keep"]:
            shutil.rmtree(work, ignore_errors=True)
    row["runtime_s"] = round(time.time() - t0, 2)
    row["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
    return row, frames


# ---------------------------------------------------------------- results files

def load_results(out_dir):
    """run_id -> latest row. Tolerates a torn last line (power cut mid-write)."""
    path = os.path.join(out_dir, "results.csv")
    res = {}
    if not os.path.exists(path):
        return res
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        head = next(rd, None)
        if not head:
            return res
        for r in rd:
            if len(r) != len(head):
                continue
            d = dict(zip(head, r))
            if d.get("run_id") and d.get("status") in ("ok", "failed"):
                res[d["run_id"]] = d
    return res


def upgrade_header(path, cols):
    """results.csv written by an older version: rewrite it once with the new header (new columns empty)."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    head = rows[0] if rows else []
    if head == cols or not head:
        return
    out = [cols] + [[dict(zip(head, r)).get(c, "") for c in cols] for r in rows[1:] if len(r) == len(head)]
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(out)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    log(f"{os.path.basename(path)}: upgraded to the {SCRIPT_VERSION} columns ({len(out) - 1} rows kept)")


def append_rows(path, cols, rows):
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    if not new:  # repair a torn last line so the next row starts clean
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                with open(path, "ab") as g:
                    g.write(b"\n")
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") for c in cols] if isinstance(r, dict) else r)
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------- run

def cmd_run(args):
    plan = load_json(os.path.join(args.out, "plan.json"), None)
    if not plan or not plan["runs"]:
        raise SystemExit(f"No plan in {args.out}. Run `plan` (or `auto`) first.")
    db = load_json(os.path.join(args.out, "sources.json"), {"sources": {}})
    done = load_results(args.out)
    pending = [r for r in plan["runs"] if done.get(r["run_id"], {}).get("status") != "ok"
               and not (args.skip_failed and done.get(r["run_id"], {}).get("status") == "failed")]
    n_ok = sum(1 for r in plan["runs"] if done.get(r["run_id"], {}).get("status") == "ok")
    log(f"{len(plan['runs'])} runs in plan, {n_ok} done, {len(pending)} to run")
    if not pending:
        return True
    for s in {r["source_key"] for r in pending}:
        if s not in db["sources"]:
            raise SystemExit(f"source {s} missing from sources.json; re-run plan")
        if not os.path.exists(db["sources"][s]["path"]):
            raise SystemExit(f"source file missing: {db['sources'][s]['path']}")
    work = args.work or os.path.join(args.out, "work")
    if os.path.isdir(work):  # leftovers of runs killed mid-way (their results were never written)
        for d in os.listdir(work):
            shutil.rmtree(os.path.join(work, d), ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    metas = {}
    for key in sorted({r["source_key"] for r in pending}):
        metas[key] = prepare(args.out, db["sources"][key])
    opt = {"out": args.out, "work": work, "keep": args.keep, "ffmpeg_version": ffmpeg_version(),
           "x264_threads": args.x264_threads, "nvenc_extra": args.nvenc_args}
    jobs = [{"run": r, "src": db["sources"][r["source_key"]], "meta": metas[r["source_key"]], "opt": opt}
            for r in pending]
    res_path, fr_path = os.path.join(args.out, "results.csv"), os.path.join(args.out, "frames.csv")
    upgrade_header(res_path, COLUMNS)
    nj = max(1, min(args.jobs, len(jobs)))
    times = [float(r["runtime_s"]) for r in done.values() if r["status"] == "ok" and r.get("runtime_s")]
    keep_awake(True)
    t_start, finished, failed = time.time(), 0, 0

    def handle(row, frames):
        nonlocal finished, failed
        append_rows(fr_path, FRAME_COLS, frames)
        append_rows(res_path, COLUMNS, [row])  # results last: a row there means its frames are on disk too
        finished += 1
        if row["status"] != "ok":
            failed += 1
        else:
            times.append(float(row["runtime_s"]))
        el = time.time() - t_start
        left = len(jobs) - finished
        eta = el / finished * left if finished else 0
        tag = "ok " if row["status"] == "ok" else "FAIL"
        msg = (f"[{finished}/{len(jobs)}] {tag} {row['source']} {row['encoder']} {row['bitrate_kbps']}k a={row['a']} "
               f"q={row['q']} ({row['picture_h']}p{', interlace ' + row['pack'] if row.get('pack') else ''})")
        if row["status"] == "ok":
            msg += f" centre {row['centre_ssim']} whole {row['whole_ssim']} marker {row['marker_pct']}%"
        else:
            msg += f" {row['error'][:160]}"
        log(msg + f" | ETA {fmt_s(eta)}")

    try:
        if not times:  # calibration: first run alone, then a real estimate
            log("timing the first run to estimate the total...")
            row, frames = do_run(jobs[0])
            handle(row, frames)
            jobs_rest = jobs[1:]
        else:
            jobs_rest = jobs
        if times:
            per = float(np.median(times))
            log(f"estimate: {len(jobs_rest)} runs x {per:.0f} s / {nj} parallel ~ {fmt_s(per * len(jobs_rest) / nj * 1.15)}"
                f" (live ETA follows; Ctrl+C any time, rerun to resume)")
        if jobs_rest:
            ctx = mp.get_context("spawn")
            sem = ctx.Manager().Semaphore(args.nvenc_parallel)
            with ctx.Pool(nj, initializer=_init_worker, initargs=(sem, args.priority == "low"), maxtasksperchild=20) as pool:
                for row, frames in pool.imap_unordered(do_run, jobs_rest):
                    handle(row, frames)
    except KeyboardInterrupt:
        log(f"stopped. {finished} runs saved this session; run again to continue where it left off.")
        return False
    finally:
        keep_awake(False)
    log(f"done: {finished} runs in {fmt_s(time.time() - t_start)}, {failed} failed")
    return failed == 0


def fmt_s(s):
    s = int(s)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


# ---------------------------------------------------------------- report

def spearman(x, y):
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    rx, ry = np.argsort(np.argsort(x)), np.argsort(np.argsort(y))
    return float(np.corrcoef(rx, ry)[0, 1])


def cmd_report(args):
    rows = list(load_results(args.out).values())
    ok = [r for r in rows if r["status"] == "ok"]
    if not ok:
        log("no finished runs yet")
        return
    for r in ok:
        for k in ("centre_ssim", "whole_ssim", "edge_ssim", "centre_psnr", "marker_pct", "mbit_s", "runtime_s",
                  "temporal_complexity", "spatial_complexity"):
            r[k] = float(r[k]) if r[k] != "" else float("nan")
        for k in ("a", "q", "bitrate_kbps", "picture_h"):
            r[k] = int(r[k])
        r["pack"] = r.get("pack", "") or ""
    cells = {}
    for r in ok:
        cells.setdefault((r["source"], r["source_class"], r["encoder"], r["bitrate_kbps"], r["excerpt_s"]), []).append(r)
    best_rows = []
    for (src, cls, enc, kb, ex), rs in sorted(cells.items()):
        packed = [r for r in rs if r["pack"]]
        rs = [r for r in rs if not r["pack"]]
        if not rs:
            continue
        base = next((r for r in rs if r["a"] == 0), None)
        bc = max(rs, key=lambda r: r["centre_ssim"])
        bw = max(rs, key=lambda r: r["whole_ssim"])
        b = {"source": src, "source_class": cls, "encoder": enc, "bitrate_kbps": kb, "excerpt_s": ex, "runs": len(rs),
             "temporal_complexity": rs[0]["temporal_complexity"], "spatial_complexity": rs[0]["spatial_complexity"],
             "best_centre_a": bc["a"], "best_centre_q": bc["q"], "best_centre_h": bc["picture_h"],
             "best_centre_ssim": bc["centre_ssim"], "best_centre_whole_ssim": bc["whole_ssim"],
             "best_whole_a": bw["a"], "best_whole_q": bw["q"], "best_whole_h": bw["picture_h"], "best_whole_ssim": bw["whole_ssim"]}
        if base:
            b["full_centre_ssim"], b["full_whole_ssim"] = base["centre_ssim"], base["whole_ssim"]
            b["gain_centre_pct"] = round(100 * (bc["centre_ssim"] / base["centre_ssim"] - 1), 2)
            b["gain_whole_pct"] = round(100 * (bw["whole_ssim"] / base["whole_ssim"] - 1), 2)
            b["chosen_beats_full_whole"] = int(bc["a"] == 0 or bc["whole_ssim"] > base["whole_ssim"])  # 1080p chosen: nothing to beat
            b["H1"] = ("yes" if b["gain_centre_pct"] >= 10 else "no") if 1000 <= kb <= 2000 else "n/a"
        else:
            b["H1"] = "no baseline"
        # H3 at the best size: strongest squeeze run vs q=0
        same = {r["q"]: r for r in rs if r["a"] == bc["a"]}
        if bc["a"] and 0 in same and max(same) > 0:
            q0, qm = same[0], same[max(same)]
            b["H3_q"] = qm["q"]
            b["H3_centre_delta"] = round(qm["centre_ssim"] - q0["centre_ssim"], 5)
            b["H3_whole_delta"] = round(qm["whole_ssim"] - q0["whole_ssim"], 5)
            b["H3"] = "yes" if b["H3_centre_delta"] > 0 and b["H3_whole_delta"] >= -0.01 else "no"
        else:
            b["H3"] = "n/a"
        # H7, interlace step: best packed run vs 1080p and vs the best size
        if packed:
            bp = max(packed, key=lambda r: r["centre_ssim"])
            b["interlace_pack"] = bp["pack"]
            b["interlace_centre_ssim"], b["interlace_whole_ssim"] = bp["centre_ssim"], bp["whole_ssim"]
            b["interlace_vs_full_pct"] = round(100 * (bp["centre_ssim"] / base["centre_ssim"] - 1), 2) if base else ""
            b["interlace_vs_best_pct"] = round(100 * (bp["centre_ssim"] / bc["centre_ssim"] - 1), 2)
            b["H7"] = "yes" if bp["centre_ssim"] >= bc["centre_ssim"] else "no"
        else:
            b["H7"] = "n/a"
        best_rows.append(b)
    bcols = list(dict.fromkeys(k for b in best_rows for k in b))
    with open(os.path.join(args.out, "best.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, bcols)
        w.writeheader()
        w.writerows(best_rows)

    # hypotheses
    hyp = {}
    h1 = [b["H1"] for b in best_rows if b["H1"] in ("yes", "no") and b["encoder"].startswith("x264")]
    cls_h1 = {}
    for b in best_rows:
        if b["H1"] in ("yes", "no"):
            cls_h1.setdefault(b["source_class"], []).append(b["H1"] == "yes")
    hyp["H1"] = ("not enough data" if not h1 else "supported" if all(x == "yes" for x in h1) else "not supported",
                 f"{sum(x == 'yes' for x in h1)}/{len(h1)} cells at 1000-2000 kbit/s gain >= 10% centre SSIM; "
                 f"classes: {', '.join(f'{c} {sum(v)}/{len(v)}' for c, v in sorted(cls_h1.items()))}")
    h2_lines, h2_ok, h2_n = [], 0, 0
    grid = {}
    for b in best_rows:
        grid.setdefault((b["encoder"], b["bitrate_kbps"], b["excerpt_s"]), []).append(b)
    for (enc, kb, ex), bs in sorted(grid.items()):
        if len({b["source_class"] for b in bs}) < 3:
            continue
        h2_n += 1
        a_set = sorted({r["a"] for r in ok if r["encoder"] == enc and r["bitrate_kbps"] == kb})
        steps = [a_set.index(b["best_centre_a"]) for b in bs]
        rho = spearman([b["temporal_complexity"] for b in bs], [b["best_centre_a"] for b in bs])
        good = max(steps) - min(steps) >= 1 and rho is not None and rho >= 0.5
        h2_ok += good
        by_src = ", ".join("%s=%s" % (b["source_class"], b["best_centre_a"]) for b in bs)
        h2_lines.append(f"{enc} {kb}k: best a by source {by_src}; "
                        f"spread {max(steps) - min(steps)} grid steps; Spearman(temporal, a) = "
                        f"{'n/a' if rho is None else f'{rho:.2f}'}")
    hyp["H2"] = ("not enough data (need >= 3 classes)" if not h2_n else "supported" if h2_ok > h2_n / 2 else "not supported",
                 f"{h2_ok}/{h2_n} encoder x bitrate cells show a spread >= 1 step that follows temporal complexity (rho >= 0.5)")
    h3 = [b["H3"] for b in best_rows if b["H3"] in ("yes", "no")]
    hyp["H3"] = ("not enough data" if not h3 else "supported" if sum(x == "yes" for x in h3) > len(h3) / 2 else "not supported",
                 f"{sum(x == 'yes' for x in h3)}/{len(h3)} cells: max allowed q beats q=0 on centre and loses <= 0.01 whole SSIM "
                 f"at the best size (q is clamped by the density floor, see q column)")
    h4_l, h4_ok = [], []
    for (src, cls, enc, kb, ex), rs in cells.items():
        if not enc.startswith("nvenc"):
            continue
        other = [v for (s2, c2, e2, k2, x2), v in cells.items() if s2 == src and k2 == kb and x2 == ex and e2.startswith("x264")]
        if not other:
            continue
        n0 = {r["a"]: r["centre_ssim"] for r in rs if r["q"] == 0}
        x0 = {r["a"]: r["centre_ssim"] for r in other[0] if r["q"] == 0}
        common = sorted(set(n0) & set(x0))
        rho = spearman([n0[a] for a in common], [x0[a] for a in common])
        if rho is None:
            continue
        ba_n, ba_x = max(common, key=n0.get), max(common, key=x0.get)
        good = rho >= 0.8 and abs(common.index(ba_n) - common.index(ba_x)) <= 1
        h4_ok.append(good)
        h4_l.append(f"{src} {kb}k: rank rho {rho:.2f}, best a nvenc {ba_n} / x264 {ba_x}")
    hyp["H4"] = ("not enough data (run --stage2 or add --encoder nvenc)" if not h4_ok else
                 "supported" if all(h4_ok) else "not supported",
                 f"{sum(h4_ok)}/{len(h4_ok)} source x bitrate cells agree (Spearman >= 0.8 on q=0 sizes, best size within one step)")
    h5 = [b for b in best_rows if b["bitrate_kbps"] <= 3000 and "chosen_beats_full_whole" in b]
    hyp["H5"] = ("not enough data" if not h5 else "supported" if all(b["chosen_beats_full_whole"] for b in h5) else "not supported",
                 f"{sum(b['chosen_beats_full_whole'] for b in h5)}/{len(h5)} cells at <= 3000 kbit/s: the best-centre size also beats 1080p on whole-frame SSIM")
    h7 = [b for b in best_rows if b["H7"] in ("yes", "no")]
    hyp["H7"] = ("not enough data (add --pack field,split)" if not h7 else
                 "supported" if sum(b["H7"] == "yes" for b in h7) > len(h7) / 2 else "not supported",
                 f"{sum(b['H7'] == 'yes' for b in h7)}/{len(h7)} cells: the interlace step (best of field / split, bwdif "
                 f"viewer) is at least as sharp in the centre as the best picture size")
    low = [r for r in ok if not r["pack"] and r["marker_pct"] < 99]
    hyp["H6"] = ("supported" if not low else "not supported",
                 f"{len(ok) - len(low)}/{len(ok)} runs read the marker on >= 99% of sampled frames")

    over = [r for r in ok if r["mbit_s"] * 1000 > 1.1 * r["bitrate_kbps"]]
    failed = [r for r in rows if r["status"] == "failed"]
    plan = load_json(os.path.join(args.out, "plan.json"), {"runs": []})
    total_rt = sum(r["runtime_s"] for r in ok)
    stamps = sorted(r["finished_at"] for r in rows if r.get("finished_at"))
    L = ["# Sweep summary (issue #18)", "",
         f"Generated {dt.datetime.now().isoformat(timespec='seconds')} by dbpo_sweep.py {SCRIPT_VERSION}. "
         f"{len(ok)} ok / {len(failed)} failed / {len(plan['runs'])} planned runs.", "",
         "## Hypotheses", "", "| | result | evidence |", "|---|---|---|"]
    L += [f"| {k} | {v[0]} | {v[1]} |" for k, v in hyp.items()]
    if h2_lines:
        L += ["", "### H2 detail", ""] + [f"- {x}" for x in h2_lines]
    if h4_l:
        L += ["", "### H4 detail", ""] + [f"- {x}" for x in h4_l]
    L += ["", "## Best picture height by source and bitrate (centre SSIM, x264)", ""]
    kbs = sorted({b["bitrate_kbps"] for b in best_rows})
    L += ["| source | class | temporal | spatial | " + " | ".join(f"{k}k" for k in kbs) + " |",
          "|---|---|---|---|" + "---|" * len(kbs)]
    for (src, cls, ex) in sorted({(b["source"], b["source_class"], b["excerpt_s"]) for b in best_rows}):
        bs = {b["bitrate_kbps"]: b for b in best_rows if b["source"] == src and b["excerpt_s"] == ex and b["encoder"].startswith("x264")}
        if not bs:
            continue
        any_b = next(iter(bs.values()))
        cell = lambda b: f"{b['best_centre_h']}p q{b['best_centre_q']} ({b.get('gain_centre_pct', '?'):+}%)" if isinstance(b.get("gain_centre_pct"), float) else f"{b['best_centre_h']}p"  # noqa: E731
        L.append(f"| {src} ({ex}s) | {cls} | {any_b['temporal_complexity']:.2f} | {any_b['spatial_complexity']:.1f} | " +
                 " | ".join(cell(bs[k]) if k in bs else "" for k in kbs) + " |")
    if h7:
        L += ["", "## Interlace step (H7): centre SSIM vs 1080p and vs the best size", "",
              "| source | encoder | kbit/s | best pack | vs 1080p | vs best size |", "|---|---|---|---|---|---|"]
        for b in h7:
            L.append(f"| {b['source']} | {b['encoder']} | {b['bitrate_kbps']} | {b['interlace_pack']} | "
                     f"{b['interlace_vs_full_pct']:+}% | {b['interlace_vs_best_pct']:+}% |")
    L += ["", "## Flagged", ""]
    for r in low:
        L.append(f"- marker {r['marker_pct']}%: {r['source']} {r['encoder']} {r['bitrate_kbps']}k a={r['a']} q={r['q']}")
    for r in over:
        L.append(f"- overshoot {r['mbit_s']:.2f} Mbit/s vs {r['bitrate_kbps']}k: {r['source']} {r['encoder']} a={r['a']} q={r['q']}")
    for r in failed:
        L.append(f"- FAILED {r['source']} {r['encoder']} {r['bitrate_kbps']}k a={r['a']} q={r['q']}: {r['error'][:200]}")
    if not (low or over or failed):
        L.append("- none")
    L += ["", "## Runtime", "",
          f"- sum of run times {fmt_s(total_rt)}; first/last result {stamps[0] if stamps else '-'} / {stamps[-1] if stamps else '-'}",
          f"- median run {float(np.median([r['runtime_s'] for r in ok])):.1f} s"]
    with open(os.path.join(args.out, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    write_manifest(args.out)
    log(f"report -> {os.path.join(args.out, 'summary.md')}, best.csv ({len(best_rows)} cells)")
    for k, v in hyp.items():
        log(f"  {k}: {v[0]}")


def write_manifest(out_dir):
    db = load_json(os.path.join(out_dir, "sources.json"), {"sources": {}})
    vend = load_json(os.path.join(VENDOR, "VENDOR.json"), {})
    man = {"script_version": SCRIPT_VERSION, "written": dt.datetime.now().isoformat(timespec="seconds"),
           "python": sys.version.split()[0], "numpy": np.__version__, "opencv": cv2.__version__,
           "platform": platform.platform(), "ffmpeg_version": ffmpeg_version(),
           "starvation_math_sha256": file_sha(os.path.join(VENDOR, "starvation_math.py")),
           "starvation_math_commit": vend.get("commit"), "vendor": vend,
           "sampling": {"every": SAMPLE_STEP, "random_seed": RAND_SEED, "random_n": RAND_N},
           "regions": {"centre": "30% x 30% centred", "edge": "outside the centred 60% x 60%",
                       "excluded": f"marker strip {_MX},{_MY} 160x32"},
           "sources": [{k: s[k] for k in ("name", "class", "path", "sha256", "excerpt_start_s", "excerpt_s", "warnings")}
                       | {"duration": s["probe"]["duration"], "probe": s["probe"]} for s in db["sources"].values()]}
    for s in man["sources"]:
        mp_ = os.path.join(out_dir, "cache", f"{s['sha256'][:12]}_{s['excerpt_start_s']}_{s['excerpt_s']}.json")
        meta = load_json(mp_, None)
        if meta:
            s["sampled_frames"] = {"step": meta["step"], "random": meta["random"]}
            s["temporal_complexity"], s["spatial_complexity"] = meta["temporal_complexity"], meta["spatial_complexity"]
    save_json(os.path.join(out_dir, "manifest.json"), man)


# ---------------------------------------------------------------- status + check

def cmd_status(args):
    plan = load_json(os.path.join(args.out, "plan.json"), {"runs": []})
    done = load_results(args.out)
    st = [done.get(r["run_id"], {}).get("status", "pending") for r in plan["runs"]]
    ok = [float(r["runtime_s"]) for r in done.values() if r["status"] == "ok"]
    pend = st.count("pending") + st.count("failed")
    log(f"{len(st)} planned: {st.count('ok')} ok, {st.count('failed')} failed, {st.count('pending')} pending")
    if ok and pend:
        log(f"median {np.median(ok):.1f} s/run; remaining ~{fmt_s(np.median(ok) * pend / max(args.jobs, 1) * 1.15)} at --jobs {args.jobs}")


def golden_selftest():
    """Vendored starvation_math reproduces its golden file, and the fast Geometry path is bit-exact with render2."""
    ok = True
    g = json.loads(json.dumps(sm.golden()))
    ref = load_json(os.path.join(VENDOR, "starvation_golden.json"), None)
    if g != ref:
        diff = [k for k in g if g.get(k) != (ref or {}).get(k)]
        print("  FAIL golden vectors differ:", diff)
        ok = False
    rng = np.random.RandomState(1)
    img = cv2.resize(sm.test_image(320, 180), (W, H), interpolation=cv2.INTER_LINEAR)
    img = np.clip(img.astype(np.int16) + rng.randint(-20, 21, img.shape), 0, 255).astype(np.uint8)
    for a, q in [(0, 0), (25, 0), (38, 3), (63, 1), (50, 2)]:
        geo = Geometry(a, q, BASE_CFG)
        fast = geo.render(img)
        slow = sm.render2(img, a, q, BASE_CFG)
        d = np.abs(fast.astype(int) - slow)
        if d.max() > 1 or (d > 0).mean() > 1e-3:  # float summation order: rare +-1 on exact .5 rounding
            print(f"  FAIL render a={a} q={q}: max diff {d.max()}, {(d > 0).mean():.2%} of samples differ")
            ok = False
        lum = cv2.cvtColor(slow, cv2.COLOR_RGB2GRAY)
        v_fast = geo.view(lum).astype(int)
        v_slow = sm.view2(slow, a, q, BASE_CFG) if a else slow
        v_slow = cv2.cvtColor(v_slow, cv2.COLOR_RGB2GRAY).astype(int)
        if np.abs(v_fast - v_slow).max() > 2:
            print(f"  FAIL view a={a} q={q}: max diff {np.abs(v_fast - v_slow).max()}")
            ok = False
        if sm.read_state(lum) != (a, q):
            print(f"  FAIL marker a={a} q={q}")
            ok = False
    return ok


def cmd_check(args):
    ok = True

    def line(good, msg, fatal=True):
        nonlocal ok
        print(("  ok   " if good else ("  FAIL " if fatal else "  warn ")) + msg)
        if fatal and not good:
            ok = False

    print("check")
    line(sys.version_info >= (3, 10), f"python {sys.version.split()[0]} (>= 3.10)")
    v = ffmpeg_version()
    line(v != "missing", f"ffmpeg: {v}")
    if v == "missing":
        print("  install: winget install Gyan.FFmpeg  (then open a new terminal)")
        return False
    line(shutil.which(ffbin("ffprobe")) is not None or os.path.exists(ffbin("ffprobe")), "ffprobe on PATH")
    for enc, fatal in (("libx264", True), ("h264_nvenc", "nvenc" in (args.encoder or ""))):  # quick4 drops NVENC if missing
        r = subprocess.run([ffbin("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={FPS}", "-frames:v", "5",
                            "-c:v", enc, "-pix_fmt", "yuv420p", "-f", "null", "-"], capture_output=True, text=True)
        line(r.returncode == 0, f"encoder {enc}" + ("" if r.returncode == 0 else ": " + (r.stderr.strip().splitlines() or ["?"])[0][:160]), fatal)
    t = time.time()
    line(golden_selftest(), f"golden self-test + fast render matches render2 ({time.time() - t:.1f} s)")
    vend = load_json(os.path.join(VENDOR, "VENDOR.json"), {})
    repo_copy = os.path.join(HERE, "..", "foveation", "starvation_math.py")
    if os.path.exists(repo_copy):
        same = file_sha(repo_copy) == file_sha(os.path.join(VENDOR, "starvation_math.py"))
        line(same, "vendored starvation_math.py matches research/foveation (run vendor.py to refresh)", fatal=False)
    print(f"  info vendored from commit {vend.get('commit', '?')}")
    out = args.out or "."
    os.makedirs(out, exist_ok=True)
    free = shutil.disk_usage(out).free / 2 ** 30
    if args.sources and os.path.isdir(args.sources):
        found = discover(args.sources)
        classes = sorted({c for _, c in found})
        line(bool(found), f"{len(found)} sources in {len(classes)} classes: {', '.join(classes)}")
        missing = [c for c in CLASSES if c not in classes]
        if missing:
            line(False, f"no clip for class(es) {', '.join(missing)} (H2 is weaker without them)", fatal=False)
        for p, c in found:
            try:
                info = probe(p)
                bad = (info["width"], info["height"]) != (W, H) or abs(info["fps"] - FPS) > 0.05
                line(not bad and info["duration"] >= 10,
                     f"{c}/{os.path.basename(p)}: {info['width']}x{info['height']} {info['fps']:.2f} fps "
                     f"{info['duration']:.1f} s {info['codec']} {info['mbit_s'] or '?'} Mbit/s", fatal=False)
            except Exception as e:  # noqa: BLE001
                line(False, f"{p}: {e}")
        need = len(found) * (20 * FPS / SAMPLE_STEP + RAND_N) * W * H / 2 ** 30 + args.jobs * 0.1
        line(free > need + 2, f"disk free {free:.0f} GiB in {os.path.abspath(out)} (reference cache needs ~{need:.1f} GiB)")
    elif args.sources:
        line(False, f"sources folder {args.sources} not found (create {args.sources}/<class>/clip.mkv)")
    print("check", "OK" if ok else "FAILED")
    return ok


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["auto", "check", "plan", "run", "report", "status"])
    ap.add_argument("--sources", default=os.path.join(HERE, "sources"))
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    ap.add_argument("--work", help="temp folder for encodes (default OUT/work); deleted per run unless --keep")
    ap.add_argument("--keep", action="store_true", help="keep each run's encoded stream")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--quick", action="store_true", help="smoke test: 2 bitrates, a 0/38/63, q 0/7 (~10 min)")
    g.add_argument("--quick4", action="store_true",
                   help="short check: busiest 4 s, 3000/6000 kbit/s, sizes + interlace step, x264 and NVENC (if present)")
    g.add_argument("--full", action="store_true", help="stage 1 screening grid (default)")
    g.add_argument("--stage2", action="store_true", help="refinement from stage 1 results + NVENC spot check")
    ap.add_argument("--a", help="scale steps, e.g. 0:63:1 or 0,25,38")
    ap.add_argument("--q", help="squeeze steps, e.g. 0:7 or 0,7")
    ap.add_argument("--bitrate", help="kbit/s list/range, e.g. 1000,2000 or 500:8000:500")
    ap.add_argument("--encoder", help="x264, nvenc, x264:<preset>, nvenc:<p1..p7> (comma list)")
    ap.add_argument("--preset", help="x264 presets, e.g. veryfast,medium")
    ap.add_argument("--pack", help="interlace step variants to add at full size: field,split (none = off)")
    ap.add_argument("--excerpt", type=float, help="excerpt length in s (default 10; 0 = whole clip)")
    ap.add_argument("--excerpt-at", default="busiest", help="busiest | middle | start | <seconds>")
    ap.add_argument("--only", help="comma list of source-name substrings to plan")
    ap.add_argument("--squish-cfg", default=BASE_CFG, help="base FSQ1 config for the squeeze (rx=ry=1: q=0 is the even shrink)")
    ap.add_argument("--floor", type=float, default=FLOOR, help="density floor that clamps q (starvation_math.max_q)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) // 6), help="parallel runs")
    ap.add_argument("--x264-threads", type=int, default=6, help="fixed per encode, so results do not depend on --jobs")
    ap.add_argument("--nvenc-parallel", type=int, default=2, help="max NVENC encodes at once")
    ap.add_argument("--nvenc-args", default="", help="extra ffmpeg args for h264_nvenc, e.g. '-spatial-aq 1'")
    ap.add_argument("--priority", choices=["low", "normal"], default="low")
    ap.add_argument("--skip-failed", action="store_true", help="do not retry runs recorded as failed")
    args = ap.parse_args()
    if args.only:
        args.only = [s.strip() for s in args.only.split(",") if s.strip()]
    if args.priority == "low":
        lower_priority()
    os.makedirs(args.out, exist_ok=True)

    if args.cmd == "check":
        sys.exit(0 if cmd_check(args) else 1)
    if args.cmd == "plan":
        cmd_plan(args)
    elif args.cmd == "run":
        ok = cmd_run(args)
        cmd_report(args)
        sys.exit(0 if ok else 1)
    elif args.cmd == "report":
        cmd_report(args)
    elif args.cmd == "status":
        cmd_status(args)
    elif args.cmd == "auto":
        if not cmd_check(args):
            sys.exit(1)
        explicit = any([args.quick, args.quick4, args.full, args.stage2, args.a, args.q, args.bitrate, args.encoder,
                        args.preset, args.only, args.pack is not None])
        if explicit or not os.path.exists(os.path.join(args.out, "plan.json")):
            cmd_plan(args)
        ok = cmd_run(args)
        cmd_report(args)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    mp.freeze_support()
    main()
