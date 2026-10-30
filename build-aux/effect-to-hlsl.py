"""Convert an OBS .effect file into plain HLSL for offline compilation.

OBS rewrites effect files before handing them to the D3D compiler; this does
the same few substitutions so CI can compile each entry point with fxc.
Usage: python effect-to-hlsl.py <in.effect> <out.hlsl>  -> prints entry points
"""
import re
import sys

src = open(sys.argv[1], encoding="utf-8").read()
cut = src.find("technique ")
techniques = src[cut:]
body = src[:cut]
body = re.sub(r"sampler_state (\w+) \{[^}]*\};", r"SamplerState \1;", body)
body = body.replace("uniform texture2d", "uniform Texture2D")
body = body.replace(": TARGET", ": SV_Target")
body = body.replace("float4 pos : POSITION", "float4 pos : SV_Position")
open(sys.argv[2], "w", encoding="utf-8").write(body)

entries = sorted(set(re.findall(r"(?:vertex|pixel)_shader\s*=\s*(\w+)\(", techniques)))
print(" ".join(entries))
