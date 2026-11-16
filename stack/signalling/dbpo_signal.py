"""Reference encoder/decoder for the DBPO1 payload and its HLS carriers. The player's decision rule is `decide`."""
import re, sys
sys.path.insert(0, "/home/claude/dbpo-obs-filter/research/foveation")
from squish_math import Squish

def encode(seq, cfg=None):
    return f"DBPO1;seq={seq};m={int(cfg is not None)}" + (f";cfg={cfg}" if cfg else "")

def parse(s):
    """-> (seq, mode, cfg). Raises ValueError on anything malformed."""
    if not s.startswith("DBPO1;"): raise ValueError("not a DBPO1 payload")
    kv = dict(p.split("=", 1) for p in s[6:].split(";"))
    seq, m = int(kv["seq"]), int(kv.get("m", 0))
    if m not in (0, 1): raise ValueError("bad mode")
    cfg = kv.get("cfg")
    if m == 1:
        if not cfg: raise ValueError("m=1 needs cfg")
        Squish.parse(cfg)
    return seq, m, cfg if m else None

def hls_tag(payload):
    seq, m, cfg = parse(payload)
    return f'#EXT-X-DBPO:SEQ={seq},M={m}' + (f',CFG="{cfg}"' if cfg else "")

def from_hls_tag(line):
    a = dict(re.findall(r'(\w+)=("[^"]*"|[^,]+)', line.split(":", 1)[1]))
    return encode(int(a["SEQ"]), a["CFG"].strip('"') if int(a["M"]) else None)

def daterange(payload, date):
    return f'#EXT-X-DATERANGE:ID="dbpo-{parse(payload)[0]}",START-DATE="{date}",X-DBPO="{payload}"'

def decide(seq, frame_w, frame_h, payloads):
    """Player rule. payloads: {seq: text} gathered from the carriers. -> ('plain'|'squish', cfg|None, counter|None)."""
    text = payloads.get(seq)
    if text is None: return "plain", None, "missing_signal"
    try:
        s, m, cfg = parse(text)
        if s != seq: raise ValueError("seq mismatch")
    except (ValueError, KeyError): return "plain", None, "bad_signal"
    return ("squish", cfg, None) if m else ("plain", None, None)
