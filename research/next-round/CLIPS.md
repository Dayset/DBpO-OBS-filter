# Clips (split fixed 2026-11-16, before any T2 result)

Rule: clips under 10 s are refused. The rest are split so that tuning and held-out both span the motion range. Settings are chosen on tuning clips only; held-out clips are run once with the chosen settings; a CNN never trains on a held-out clip. FPV clips, when they arrive, are held-out unless there are at least four, then two and two.

Motion label = mean absolute difference between consecutive frames at 480x270 grey (`list_clips.py`).

| Clip (path under c:\dbpo-lab) | Seconds | Motion | Split | sha256 |
|---|---|---|---|---|
| ref60.mkv (PlanetSide 2 spin, v3 clip) | 15.1 | 26.86 | tuning | 27c3de6e9dc8adb0a454cbfe2ded008d8b5d5437ddc0b15db0ad3d60e2245b98 |
| out\Planetside_2_2026_11_15___21_29_34_08\ref.mkv | 16.9 | 21.96 | tuning | 3adca2ad587a08bda42c7ec6d63b654847ebdc5fee192961541f71387a2e9caa |
| out\PlanetsideDifferentSpeed\ref.mkv (first 20 s of the v4.0.0 clip) | 20.0 | 11.85 | tuning | 9630ba74e549da9542e32112b28e376d62ad1ebb7ee5f11fe7fff9171b83c0d1 |
| out\Planetside_2_2026_11_15___21_26_27_05\ref.mkv (calm, control) | 13.1 | 0.96 | tuning | 1cf3ead5d1b7283702c8634c49fdb74781bbe6ae2bad71397e202332f408218e |
| out\Planetside_2_2026_11_15___21_39_25_09\ref.mkv | 11.4 | 13.23 | held-out | d4bf88878015697ba7fb3c863a3d70e54cc3574840bb818b6049fee7b57d3e83 |
| out\X264\ref.mkv | 20.0 | 14.92 | held-out | 24dccb55f3916c273f98bd513bdac081cd8bbd5d4691382b8c75bdd48ad74e3a |
| game2_ref.mkv (from game2.mp4, 60 s, not PlanetSide) | 60 | not yet measured | held-out | not yet computed |
| out\Planetside_2_2026_11_15___21_29_07_06\ref.mkv | 8.6 | 17.10 | refused (under 10 s) | |
| out\Planetside_2_2026_11_15___21_29_27_07\ref.mkv | 2.1 | 31.01 | refused (under 10 s) | |

Notes
- The calm clip (motion 0.96) is in tuning on purpose: the method should not be switched on there, and T3 needs it.
- DifferentSpeed was in the earlier CNN training set. It stays tuning, so no held-out claim rests on it.
- All clips except game2 are PlanetSide 2. Held-out is therefore only partly independent content until game2 and FPV are in.
