# Per-element UI restoration: research (issue #32)

Question: can a small, game-specific restorer (about 10k parameters, one per UI element type) sharpen HUD elements such as the minimap, health and shield bars, the ammo count and the crosshair on the viewer side after a starved stream, and can it ship as an optional package the viewer installs with consent?

Status: **desk research and plan.** Nothing in this file is measured yet, except where it cites the existing results in `research/restore/` and `research/foveation/`. Numbers marked *ESTIMATE* are arithmetic, not tests. The measurement work is #32 UI.2–UI.4.

## 1. Why UI is a different problem from the picture

- **Fixed pixels.** Health bars, ammo count, and the crosshair sit at fixed positions, so their content changes only where the value changes. A restorer can use the element's own history.
- **Known shape.** Bars are solid fills with hard edges. Digits come from one game font. Both can be checked against a template.
- **Readability is the goal.** Viewers read these elements in fractions of a second. A small error in a digit ("8" vs "6") matters more than a PSNR gain in the scene.
- **Crispness over fidelity** (already a stated project preference): UI edges that differ slightly from the original are acceptable if they are not blurred.

Under starvation (the 540p / 360p case from #20 and #17), the general restorer blurs exactly these details. The 540p and 360p results in `research/restore/README.md` show edge sharpness recovering in the scene, but not for text or thin bars.

## 2. Element survey (UI.1, partly from the repo)

| Element | Typical anchor | Size (1080p, ESTIMATE) | Motion | Update rate | Main risk at low bitrate |
|---|---|---|---|---|---|
| Health / shield bars | fixed, bottom | 200–400 × 20–40 px | fill changes in steps | on hit | bar edge blur; value misread |
| Ammo count | fixed, bottom right | 60–120 × 30–50 px | digits change | on shot | digit confusion |
| Minimap | fixed corner | 200–300 px square | scrolls with the player | every frame | map texture blur, marker loss |
| Crosshair | screen centre | 20–40 px | fixed | never | thin line breaks up |
| Chat / kill feed | fixed, top or left | text lines | new lines appear | on event | text legibility |

Interaction with the squish (see #17 / atlas work): these elements usually sit in the periphery, where Foveated Squish takes the most pixels away. The texture-atlas feature in the project (user regions get at least 50% less squish) is the other way to protect them. Restoration and the atlas are alternatives for the same regions. The atlas costs bits; restoration costs viewer compute. The benchmark in UI.4 should compare them, not assume restoration wins.

## 3. Method options

| Option | What it is | Viewer cost | Risk |
|---|---|---|---|
| A. General restorer (#20) | one model for the whole frame | 10k params per frame | blurs text and thin bars |
| B. Per-element CNN (10k) | one small model per element type, input = element crop plus its previous crop | ESTIMATE: under 1 MMAC per crop per frame | overfits one skin; must be retrained per patch |
| C. Template / glyph reconstruction | read the value (digit OCR, bar fraction) and re-draw the element from the game font or a bar template | very low | fails if the font or skin changes; needs a value reader |
| D. Atlas (existing project feature) | protect the region in the encoder with less squish | ~0 on viewer; costs bits | costs bitrate in starved scenes |

Recommendation for UI.4: benchmark **A, B, C, D** side by side on the same clips. C is included deliberately: for digits and bars, a re-draw from a known template is likely more accurate than any learned model, and it is cheap enough to ship without a download. If C wins for digits and bars, B is needed only for the minimap and the chat, which have no template.

## 4. Package and consent (UI.5, design only)

- **Size.** ESTIMATE: 10k params in float16 is about 20 KB per element model; int8 about 10 KB. Five element models plus a small loader are under 200 KB, well under what a browser extension or userscript should carry.
- **When to load.** Only after the viewer accepts an install prompt, and only when the streamer's share code names that game profile. The unsquish extension already acts only on recognised codes (project preference), so the same gate applies.
- **What the viewer controls.** Per-element on/off, a strength slider, and a "show original" toggle. Template reconstruction (C) must show the original when the value read is uncertain.
- **Legal and anti-cheat.** The package works only from the decoded picture. It does not read game memory, game files, or network traffic. This keeps it on the same side as an OBS filter and should be stated in the package README.
- **Patch drift.** HUD changes break both B and C. The package needs a version tag per game build and a fallback to bicubic when the profile does not match.

## 5. Plan (matches the issue work packages)

1. **UI.1** Element survey for PlanetSide 2 first, then two other games (sandbox, writing).
2. **UI.2** Crop UI regions from the repo clips, run the degradation chain at 250 / 300 kbit/s, measure bicubic and bicubic + unsharp on element boxes (sandbox).
3. **UI.3** Train one 10k model per element type on crops only, reusing `research/restore/train_float.py` (sandbox, resumable).
4. **UI.4** Compare A / B / C / D on PSNR and SSIM in the element box, plus digit OCR accuracy for ammo and health (sandbox, ≤3 min per arm).
5. **UI.5** Package and consent design (writing only).
6. **UI.6** Write-up and go/no-go in this file and as a comment on #20.

## 6. Decision criteria (from the issue acceptance)

- Per-element B beats A on the element box by ≥ 0.5 dB PSNR, **or** digit OCR gains ≥ 5 points. Otherwise say so and stop.
- C is reported separately for digits and bars, with its failure rate on skin changes.
- Atlas (D) is reported as the bitrate cost of protecting the same region, so the trade is visible.

## 7. Open questions

- Which game UIs can the user capture losslessly (PlanetSide 2 is the only one with footage in the repo so far)?
- Does the streamer want restoration at all, or only the atlas? The atlas needs no viewer install.
- Should the element models be trained on the streamer's own skin, with the share code carrying a skin ID?
