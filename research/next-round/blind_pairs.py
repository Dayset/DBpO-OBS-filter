"""Blind pair test helper.

  python blind_pairs.py make PAIRS_DIR OUT_DIR SEED
      PAIRS_DIR holds one folder per clip, each with two files: plain.mp4 and squish.mp4.
      Writes OUT_DIR/p01_L.mp4 and p01_R.mp4 with the arm order randomised per pair, and
      OUT_DIR_KEY/key.json (keep this file away from viewers). Viewers rate p01 and so on.
  python blind_pairs.py tally KEY_JSON ANSWERS_CSV
      ANSWERS_CSV columns: pair,viewer,centre,sides   where centre and sides are L, R or same.
      Prints squish-preference counts and a two-sided sign test per question (ties dropped).
"""
import csv, json, math, os, random, shutil, sys

def make(pairs_dir, out_dir, seed):
    rng = random.Random(int(seed))
    key_dir = out_dir + "_KEY"
    os.makedirs(out_dir, exist_ok=True); os.makedirs(key_dir, exist_ok=True)
    key = {}
    for i, clip in enumerate(sorted(os.listdir(pairs_dir)), 1):
        pid = f"p{i:02d}"
        src = {"plain": os.path.join(pairs_dir, clip, "plain.mp4"), "squish": os.path.join(pairs_dir, clip, "squish.mp4")}
        left, right = ("plain", "squish") if rng.random() < 0.5 else ("squish", "plain")
        shutil.copy(src[left], os.path.join(out_dir, f"{pid}_L.mp4"))
        shutil.copy(src[right], os.path.join(out_dir, f"{pid}_R.mp4"))
        key[pid] = {"clip": clip, "L": left, "R": right}
    json.dump(key, open(os.path.join(key_dir, "key.json"), "w"), indent=1)
    print("made", len(key), "pairs; key in", key_dir)

def sign_p(wins, n):
    k = max(wins, n - wins)
    p = sum(math.comb(n, j) for j in range(k, n + 1)) / 2 ** n
    return min(1.0, 2 * p)

def tally(key_json, answers_csv):
    key = json.load(open(key_json))
    rows = list(csv.DictReader(open(answers_csv)))
    for q in ("centre", "sides"):
        sq = pl = same = 0
        for r in rows:
            ans = r[q].strip().upper()
            k = key[r["pair"]]
            if ans == "SAME": same += 1; continue
            side = "L" if ans == "L" else "R"
            if key[r["pair"]][side] == "squish": sq += 1
            else: pl += 1
        n = sq + pl
        p = sign_p(sq, n) if n else float("nan")
        print(f"{q}: squish {sq}, plain {pl}, same {same}, sign test p={p:.3f}")

if __name__ == "__main__":
    if sys.argv[1] == "make": make(*sys.argv[2:5])
    elif sys.argv[1] == "tally": tally(*sys.argv[2:4])
