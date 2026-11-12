"""Reference forward pass for the edge restorer (issue #21, spec: SPEC.md).

    python3 research/restore/restore_math.py --golden   # writes restore_golden.json
    python3 research/restore/restore_math.py --check    # checks the committed golden file
"""
import hashlib
import json
import os
import struct
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "restore_golden.json")


def conv3x3(x, w, b):
    """x: (Cin,H,W); w: (Cout,Cin,3,3); b: (Cout,). Zero padding 1."""
    c, h, wd = x.shape
    xp = np.pad(x, ((0, 0), (1, 1), (1, 1)))
    out = np.zeros((w.shape[0], h, wd), np.float32) + b[:, None, None]
    for i in range(3):
        for j in range(3):
            out += np.einsum("oc,chw->ohw", w[:, :, i, j], xp[:, i:i + h, j:j + wd])
    return out


def pixel_shuffle(x, k):
    """(C·k², H, W) → (C, H·k, W·k), channel index c·k² + i·k + j."""
    c2, h, w = x.shape
    c = c2 // (k * k)
    return x.reshape(c, k, k, h, w).transpose(0, 3, 1, 4, 2).reshape(c, h * k, w * k)


def load(path):
    """Read the RSTR v1 format (SPEC.md §4) → dict name → float32 array."""
    with open(path, "rb") as f:
        assert f.read(4) == b"RSTR", "bad magic"
        version, hlen = struct.unpack("<II", f.read(8))
        assert version == 1, f"unsupported version {version}"
        header = json.loads(f.read(hlen).decode("utf-8"))
        data = f.read()
    out = {}
    for t in header["tensors"]:
        raw = data[t["offset"]:t["offset"] + t["nbytes"]]
        if t["dtype"] == "f32":
            arr = np.frombuffer(raw, "<f4").astype(np.float32)
        elif t["dtype"] == "i8":
            arr = np.frombuffer(raw, np.int8).astype(np.float32) * t["scale"]
        else:
            raise ValueError(t["dtype"])
        out[t["name"]] = arr.reshape(t["shape"])
    out["_width"] = header["width"]
    return out


def export_state(tensors, path, dtype="f32"):
    """Write a dict name → array in RSTR v1 format. dtype 'f32' or 'i8' (per-tensor scale = max|w|/127)."""
    header = {"width": tensors["_width"], "tensors": []}
    blobs, offset = [], 0
    for name in sorted(k for k in tensors if not k.startswith("_")):
        arr = np.asarray(tensors[name], np.float32)
        if dtype == "i8" and name.endswith(".w"):
            scale = float(np.abs(arr).max() / 127) or 1.0
            blob = np.clip(np.round(arr / scale), -127, 127).astype(np.int8).tobytes()
            dt = "i8"
        else:
            scale, blob, dt = 1.0, arr.astype("<f4").tobytes(), "f32"
        header["tensors"].append({"name": name, "dtype": dt, "shape": list(arr.shape),
                                  "offset": offset, "nbytes": len(blob), "scale": scale})
        blobs.append(blob)
        offset += len(blob)
    hj = json.dumps(header).encode("utf-8")
    with open(path, "wb") as f:
        f.write(b"RSTR" + struct.pack("<II", 1, len(hj)) + hj + b"".join(blobs))


def restore(img, t, s_int):
    """img: (H,W,3) float32 in [0,1] (the source picture). Returns (H·s, W·s, 3) float32 in [0,1].

    s_int = ceil(s). The output is produced at s_int × source size; resampling to the exact level
    size is a separate step (SPEC §2) and is not done here.
    """
    x = img.transpose(2, 0, 1).astype(np.float32)
    h, w = img.shape[:2]
    base = cv2.resize(img, (w * s_int, h * s_int), interpolation=cv2.INTER_CUBIC).transpose(2, 0, 1)
    y = x
    for k in (1, 2, 3):
        y = np.maximum(conv3x3(y, t[f"conv{k}.w"], t[f"conv{k}.b"]), 0)
    y = conv3x3(y, t["conv4.w"], t["conv4.b"])
    y = pixel_shuffle(y, s_int) + base
    return np.clip(y, 0, 1).transpose(1, 2, 0)


def random_tensors(width, s_int, seed):
    rng = np.random.default_rng(seed)
    t = {"_width": width}
    shapes = {"conv1": (width, 3, 3, 3), "conv2": (width, width, 3, 3),
              "conv3": (width, width, 3, 3), "conv4": (3 * s_int * s_int, width, 3, 3)}
    for name, shp in shapes.items():
        fan_in = shp[1] * 9
        t[f"{name}.w"] = (rng.standard_normal(shp) / np.sqrt(fan_in)).astype(np.float32)
        t[f"{name}.b"] = (rng.standard_normal(shp[0]) * 0.01).astype(np.float32)
    return t


def golden():
    seed, width, s_int = 7, 20, 2
    t = random_tensors(width, s_int, seed)
    rng = np.random.default_rng(seed + 1)
    img = rng.random((90, 160, 3), dtype=np.float32)  # 160×90 source → 320×180 output
    out = restore(img, t, s_int)
    q = np.round(out.astype(np.float64), 6)
    return {
        "seed": seed, "width": width, "s_int": s_int,
        "input_shape": list(img.shape), "output_shape": list(out.shape),
        "output_sha256_of_1e6_rounded_f64": hashlib.sha256(q.tobytes()).hexdigest(),
        "output_sum": float(q.sum()),
        "samples": {f"{y},{x},{c}": float(q[y, x, c])
                    for (y, x, c) in [(0, 0, 0), (45, 80, 1), (179, 319, 2), (90, 160, 0), (12, 200, 2)]},
    }


if __name__ == "__main__":
    if "--golden" in sys.argv:
        g = golden()
        json.dump(g, open(GOLDEN, "w"), indent=2)
        print(json.dumps(g, indent=2))
    elif "--check" in sys.argv:
        ref = json.load(open(GOLDEN))
        cur = golden()
        ok = cur == ref
        print("OK" if ok else f"MISMATCH\nref={ref}\ncur={cur}")
        sys.exit(0 if ok else 1)
    else:
        print(__doc__)
