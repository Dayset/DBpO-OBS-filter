"""Refresh vendor/ from research/foveation (the sweep runs without the repo, so it carries its own copy).

    python vendor.py
"""
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "foveation")
FILES = ["starvation_math.py", "atlas_math.py", "squish_math.py", "starvation_golden.json"]
EXTRA = {"fieldpack_math.py": os.path.join(HERE, "..", "fieldpack")}  # file -> folder it comes from

if __name__ == "__main__":
    os.makedirs(os.path.join(HERE, "vendor"), exist_ok=True)
    for f in FILES:
        shutil.copy2(os.path.join(SRC, f), os.path.join(HERE, "vendor", f))
    for f, d in EXTRA.items():
        shutil.copy2(os.path.join(d, f), os.path.join(HERE, "vendor", f))
    try:
        commit = subprocess.run(["git", "-C", SRC, "log", "-1", "--format=%H", "--", *FILES], capture_output=True,
                                text=True).stdout.strip() or None
    except OSError:
        commit = None
    meta = {"commit": commit, "copied": dt.date.today().isoformat(), "from": "research/foveation",
            "sha256": {f: hashlib.sha256(open(os.path.join(HERE, "vendor", f), "rb").read()).hexdigest()
                       for f in FILES + list(EXTRA)}}
    json.dump(meta, open(os.path.join(HERE, "vendor", "VENDOR.json"), "w"), indent=1)
    print("vendored", ", ".join(FILES), "from commit", commit)
