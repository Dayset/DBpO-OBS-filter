"""Viewer colour look: numpy model of the browser shader (viewer/unsquish-extension/squish.js,
LOOK_COLOR_SHADER) and the packed look code that carries all look values in a stream title.

Source of truth for the colour math and the code format; the JS (and later C) versions are
checked against it. All colours are display (sRGB-encoded) values 0..1; the filters are
inspired by NVIDIA Freestyle but are not bit-identical to it.

Look values (unsigned 0..1 unless marked signed -1..1):
  Details/sharpen: sharpen, clarity, hdr, bloom, sharpenPlus
  Colour: brightness, contrast, gamma, saturation, vibrance, temperature, tint, hue, highlights,
          shadows (all signed), bw, sepia, night (unsigned)

Look code: "※" + CJK characters U+4E00.. (14 bits each). Bit stream: 2-bit version (1), then
each field below, 7 bits, MSB first, padded with zero bits. Signed q = floor(v*63 + 0.5) + 64;
unsigned q = floor(v*127 + 0.5). Decode: signed (q-64)/63, unsigned q/127.
"""
import math

import numpy as np

# (name, signed) in code order
FIELDS = [
    ("sharpen", False), ("clarity", False), ("hdr", False), ("bloom", False), ("sharpenPlus", False),
    ("brightness", True), ("contrast", True), ("gamma", True), ("saturation", True), ("vibrance", True),
    ("temperature", True), ("tint", True), ("hue", True), ("highlights", True), ("shadows", True),
    ("bw", False), ("sepia", False), ("night", False),
]
COLOR_FIELDS = ["brightness", "contrast", "gamma", "saturation", "vibrance", "temperature", "tint", "hue",
                "highlights", "shadows", "bw", "sepia", "night"]
MARK = "※"
CJK0 = 0x4E00
VERSION = 1


def _clamp(v, signed):
    v = float(v or 0.0)
    return max(-1.0, min(1.0, v)) if signed else max(0.0, min(1.0, v))


def _q(v, signed):
    v = _clamp(v, signed)
    return int(math.floor(v * 63 + 0.5)) + 64 if signed else int(math.floor(v * 127 + 0.5))


def pack(look):
    """Look dict -> code string ("" when every value is 0)."""
    if all(_q(look.get(k, 0.0), s) == (64 if s else 0) for k, s in FIELDS):
        return ""
    bits = [(VERSION >> 1) & 1, VERSION & 1]
    for k, s in FIELDS:
        q = _q(look.get(k, 0.0), s)
        bits += [(q >> i) & 1 for i in range(6, -1, -1)]
    while len(bits) % 14:
        bits.append(0)
    out = ""
    for i in range(0, len(bits), 14):
        n = 0
        for b in bits[i:i + 14]:
            n = (n << 1) | b
        out += chr(CJK0 + n)
    return MARK + out


def unpack(code):
    """Code string (with or without the mark) -> look dict, or None if it is not a valid code."""
    if code.startswith(MARK):
        code = code[1:]
    need = 2 + 7 * len(FIELDS)
    if len(code) * 14 < need:
        return None
    bits = []
    for ch in code:
        n = ord(ch) - CJK0
        if not 0 <= n < (1 << 14):
            return None
        bits += [(n >> i) & 1 for i in range(13, -1, -1)]
    if (bits[0] << 1 | bits[1]) != VERSION:
        return None
    look = {}
    pos = 2
    for k, s in FIELDS:
        q = 0
        for b in bits[pos:pos + 7]:
            q = (q << 1) | b
        pos += 7
        look[k] = (q - 64) / 63.0 if s else q / 127.0
    return look


def quantize(look):
    """What a viewer gets after a pack/unpack round trip."""
    return unpack(pack(look)) or {k: 0.0 for k, _ in FIELDS}


# ---------- the colour pass (numpy model of the shader) ----------

W709 = np.array([0.2126, 0.7152, 0.0722])


def _smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def apply_color(img, look):
    """img: float array (h, w, 3) in 0..1. Returns the colour-corrected picture."""
    g = lambda k: float(look.get(k, 0.0))  # noqa: E731
    c = img.astype(np.float64).copy()
    # white balance
    c[..., 0] += g("temperature") * 0.10 + g("tint") * 0.04
    c[..., 2] += -g("temperature") * 0.10 + g("tint") * 0.04
    c[..., 1] -= g("tint") * 0.08
    # highlights / shadows by luma
    l = np.clip(c @ W709, 0.0, 1.0)[..., None]
    c = c + g("highlights") * 0.25 * _smoothstep(0.5, 1.0, l) + g("shadows") * 0.25 * (1.0 - _smoothstep(0.0, 0.5, l))
    # brightness, contrast, gamma
    c = c + g("brightness") * 0.5
    c = (c - 0.5) * (1.0 + g("contrast")) + 0.5
    c = np.clip(c, 0.0, 1.0)
    c = np.power(c, 2.0 ** (-g("gamma")))
    # hue: rotation in YIQ
    if g("hue") != 0.0:
        a = g("hue") * math.pi
        ca, sa = math.cos(a), math.sin(a)
        y = c @ np.array([0.299, 0.587, 0.114])
        i = c @ np.array([0.596, -0.274, -0.322])
        q = c @ np.array([0.211, -0.523, 0.312])
        i2, q2 = i * ca - q * sa, i * sa + q * ca
        c = np.stack([y + 0.956 * i2 + 0.621 * q2, y - 0.272 * i2 - 0.647 * q2, y - 1.106 * i2 + 1.703 * q2], axis=-1)
        c = np.clip(c, 0.0, 1.0)
    # saturation and vibrance (vibrance lifts dull colours more than vivid ones)
    l = (c @ W709)[..., None]
    sat = (c.max(axis=-1) - c.min(axis=-1))[..., None]
    f = np.maximum(1.0 + g("saturation") + 1.5 * g("vibrance") * (1.0 - sat), 0.0)
    c = np.clip(l + (c - l) * f, 0.0, 1.0)
    # effects
    l = (c @ W709)[..., None]
    c = c + (l - c) * g("bw")
    sep = np.stack([c @ [0.393, 0.769, 0.189], c @ [0.349, 0.686, 0.168], c @ [0.272, 0.534, 0.131]], axis=-1)
    c = c + (np.clip(sep, 0.0, 1.0) - c) * g("sepia")
    night = np.clip(np.power(c, 0.8) * np.array([0.7, 0.85, 1.15]), 0.0, 1.0)
    c = c + (night - c) * g("night")
    return np.clip(c, 0.0, 1.0)


def selftest():
    fails = []
    look = {k: 0.0 for k, _ in FIELDS}
    if pack(look) != "":
        fails.append("empty look must pack to nothing")
    look.update(sharpen=0.4, vibrance=0.5, hue=-0.25, night=1.0, brightness=-1.0, contrast=1.0)
    code = pack(look)
    back = unpack(code)
    if back is None or len(code) != 1 + 10:
        fails.append(f"code length {len(code)} (want 11 with the mark)")
    else:
        for k, s in FIELDS:
            if abs(back[k] - _clamp(look.get(k, 0.0), s)) > (0.5 / 63 if s else 0.5 / 127) + 1e-9:
                fails.append(f"round trip {k}: {look.get(k)} -> {back[k]}")
    if unpack("※abc") is not None or unpack("※" + chr(CJK0)) is not None:
        fails.append("garbage accepted")
    flat = np.full((2, 2, 3), 0.4)
    if np.abs(apply_color(flat, {}) - flat).max() > 1e-12:
        fails.append("zero look must not change the picture")
    return not fails, fails


def golden():
    rng = np.random.default_rng(7)
    cases = []
    for i in range(6):
        look = {k: round(float(rng.uniform(-1 if s else 0, 1)), 3) if rng.random() < 0.6 else 0.0 for k, s in FIELDS}
        cases.append({"look": look, "code": pack(look), "decoded": quantize(look)})
    return cases


if __name__ == "__main__":
    import json
    import sys
    if "--golden" in sys.argv:
        print(json.dumps(golden(), ensure_ascii=False, indent=1))
    else:
        ok, fails = selftest()
        print(json.dumps({"ok": ok, "failures": fails}, ensure_ascii=False))
        sys.exit(0 if ok else 1)
