import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from dbpo_signal import encode, parse, hls_tag, from_hls_tag, daterange, decide
from squish_math import Squish, Axis
cfg = Squish(Axis(r=0.75, c=0.15, f=0.5, p=1), Axis(r=0.75, c=0.15, f=0.5, p=1), 1.7778).to_string()
V = json.load(open(os.path.join(os.path.dirname(__file__), "vectors.json")))
assert V["cfg"] == cfg, (V["cfg"], cfg)
for v in V["encode"]:
    p = encode(v["seq"], v["cfg"]); assert p == v["payload"], p
    assert parse(p) == (v["seq"], int(v["cfg"] is not None), v["cfg"])
    assert hls_tag(p) == v["hls"], hls_tag(p); assert from_hls_tag(v["hls"]) == p
assert daterange(V["encode"][0]["payload"], "2026-11-15T20:00:00Z") == V["daterange"]
for bad in V["malformed"]:
    try: parse(bad); raise SystemExit(f"accepted {bad}")
    except ValueError: pass
for d in V["decide"]:
    got = decide(d["seq"], 1440, 810, {int(k): t for k, t in d["payloads"].items()})
    assert list(got) == d["expect"], (d, got)
print("ok:", len(V["encode"]), "payloads,", len(V["malformed"]), "malformed,", len(V["decide"]), "player decisions")
