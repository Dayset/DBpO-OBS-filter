# DBpO Unsquish (Chrome / Edge extension)

Unfolds streams made with the [Foveated Squish](../../plugins/foveated-squish/README.md) OBS filter: the page's video is redrawn through a WebGL shader at its original shape, sharp center 1:1 and the squeezed edges stretched back out. It works on any site whose player uses a normal `<video>` element (Twitch, YouTube, Kick, self-hosted players), including fullscreen.

## Install (unpacked)

1. Download this folder (`viewer/unsquish-extension`) from the repository, or from the CI artifact.
2. Open `chrome://extensions` (or `edge://extensions`) and turn on **Developer mode**.
3. Click **Load unpacked** and pick the `unsquish-extension` folder.

## Use

Click the extension icon:

- **Paste the streamer's config** (`FSQ1:rx=…`). It applies immediately to the playing video, no Save click or page refresh needed. A pasted config always wins.
- Or leave **Auto-detect** on: if the streamer puts the `FSQ1:…` string in the stream or page title, it is picked up automatically.
- **Unsquish videos** turns the whole thing off and on.

The status line in the popup says which config is in use, or why a pasted string was rejected.

## Look (like NVIDIA Freestyle)

Besides sharpening, the look has colour correction: Brightness, Contrast, Gamma, Saturation, Vibrance, Temperature, Tint, Hue, Highlights, Shadows, Black & white, Sepia and Night mode (all in the popup; the streamer's values arrive as a short look code in the title and you can switch to your own). The colour pass runs first, before Details and Sharpen+; its math is `research/foveation/color_math.py`. Inspired by NVIDIA Freestyle, not a copy.

The popup's **Look** box adds the NVIDIA Freestyle "Details" and "Sharpen+" look on top of the video, in your browser. It costs the streamer no bitrate (the stream itself is untouched).

- **Look**: *Streamer's look* (default: whatever the streamer set in OBS, sent inside the `FSQ1:` string; nothing if they set none), *My look* (your own sliders), or *Off*.
- **Sharpen**, **Clarity**, **HDR toning**, **Bloom**: NVIDIA's Details filter, 0–100%.
- **Sharpen+**: NVIDIA's Sharpen+ strength, 0–100%.
- **Also use my look on normal videos**: off by default. When on, *My look* is also drawn on ordinary videos that have no squish config (only when the site allows WebGL to read the video; otherwise that one video is left alone).

How close it is to NVIDIA:

| Slider | What it does | Match |
|---|---|---|
| Sharpen | Sharpens fine edges, limited so edges don't ring. | Same formula as NVIDIA's Details shader (details.yfx). |
| Clarity | Boosts mid-size local contrast (textures, shapes) with a soft-light blend against a wide blur. | Same formula. |
| HDR toning | Lifts shadows and pulls highlights toward local average: a punchier, "HDR-ish" look. | Same formula. |
| Bloom | Soft glow around bright areas (screen blend of a wide blur). | Same formula. |
| Sharpen+ | Adaptive sharpening that sharpens soft areas more and already-sharp edges less. | **Approximation**: NVIDIA's Sharpen+ code isn't public; this is AMD FidelityFX CAS, the same family of contrast-adaptive sharpening. |

Details (Sharpen/Clarity/HDR toning/Bloom) is a line-by-line port of NVIDIA's shader math (15-step Gaussian, 8-tap box blur, the same blend curves and constants). Two deliberate differences: the wide blur is computed at half resolution and its radius is scaled to the video height (1080p = NVIDIA's own size), so the look is the same at any player size and stays cheap; and the output is clamped/guarded against NaN. Expect a very close but not bit-identical result to Freestyle on the same frame.

Cost: about 36 texture reads per pixel in 4 short passes (blur H and V at half size, Details, Sharpen+), only when a slider is above 0. That is small for any GPU from the last decade and should hold 60 fps at 1080p–1440p (120 fps on a mid-range card). With every slider at 0 or Look off, the old single pass runs and nothing extra is done. If WebGL can't set up the look passes, the extension falls back to the plain picture.

The page's `<html>` element gets `data-dbpo-look="sharpen,clarity,hdr,bloom,sharpen+"` while a look is on (and `data-dbpo-unsquish="plain"` on a plain video), for debugging.

After installing or updating the extension, open tabs are switched to the new version automatically.

## How it works

`content.js` finds the largest playing `<video>`, sets it to `opacity: 0` (it keeps playing, with sound intact) and puts a canvas directly above it, below the player's own controls (they stay visible and clickable), sized to the source aspect ratio. Every animation frame the video is uploaded as a texture and drawn with `squish.js`'s fragment shader, which maps each screen pixel to its position in the squished frame. The math is identical to the OBS shader; `tests/compare.js` checks it against the Python reference (worst difference 5e-13), and `tests/e2e.py` plays a squished VP9 video in headless Chromium and checks the canvas against the reference viewer (0.5/255 mean difference, landmarks within 0.02 px). `tests/look.py` checks the look against a numpy model of the same passes (0/255 difference) for the streamer's look, your own look, look off and plain videos; `tests/extension.py` loads the real extension, sets the look through the popup and checks that player controls stay on top and clickable. CI runs all of them (`run-tests.sh --e2e`).

Limits: videos served cross-origin without CORS can't be read by WebGL; the extension then turns itself off for that page and logs a warning. DRM-protected video (not used by live streams) can't be read either.

## Chat signal (test)

On twitch.tv the extension listens to the channel owner's chat for `fsq:on:<n>` and `fsq:off:<n>`, and switches the unsquish on or off with a delay (`chatDelayMs` in extension storage, default 1500 ms). Only the channel owner's account counts. Before the first signal nothing changes. `<n>` must increase each time.

## Sharp areas (atlas)

When the streamer marks HUD areas (a minimap, ammo, a webcam) as "Keep sharp", the config string carries region keys (`gx`…`gs`, then `h`, `i`, `j`). The stream then has the squished picture on the left and a strip on the right holding each area at about full size. The extension unfolds the picture as usual and, in the same shader pass, draws each area from its slot over its place in the picture, so the minimap is crisp while the rest of the edges stay squeezed. The popup status says how many sharp areas the config has, e.g. "…, 2 sharp areas".

- The look (sharpen, clarity, ...) runs once over the finished picture, so areas and surroundings match.
- Works at any player size and on lower-resolution transcodes: slots are in normalized coordinates.
- Without the extension a viewer sees the strip on the right and, if the streamer hides the duplicate, a flat patch where the area is.
- Older extension versions ignore the region keys and show the strip as part of the picture.
- Tests: `tests/atlas.js` (layout vs the golden vectors), `tests/e2e_atlas.py` (plays an atlas frame in headless Chromium and compares with the reference viewer).

See `plugins/foveated-squish/README.md` for the streamer's side.

## Phones (Android and iPhone)

Chrome on a phone can't run extensions, and the Twitch app can't run anything, so watch in a browser that can.

- **iPhone (Safari):** install a userscript manager (the free "Userscripts" app from the App Store, then enable it in Settings > Safari > Extensions), open `https://raw.githubusercontent.com/Dayset/DBpO-OBS-filter/main/viewer/userscript/dbpo-unsquish.user.js` in Safari and install the script. No developer account is needed.
- **Android:** Firefox for Android with a userscript add-on (Tampermonkey or Violentmonkey), same script. Or build the extension itself for Firefox: `python3 viewer/unsquish-extension/build.py --firefox` writes `dist/dbpo-unsquish-firefox.zip` (Firefox release builds only install signed add-ons, so use Firefox Nightly or a developer build, or sign it on addons.mozilla.org).

The userscript is generated from this folder by `build.py` (never edit it by hand) and runs on twitch.tv by default; add an `@match` line for other sites. It has the same shader, config and chat on/off as the extension. Settings are in a small round button in the lower-left corner of the page instead of the toolbar popup. The test `tests/e2e_userscript.py` checks it in headless Chromium. It has not been tried on a real phone yet.
