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

After installing or updating the extension, open tabs are switched to the new version automatically.

## How it works

`content.js` finds the largest playing `<video>`, sets it to `opacity: 0` (it keeps playing, with sound intact) and puts a canvas directly above it, below the player's own controls (they stay visible and clickable), sized to the source aspect ratio. Every animation frame the video is uploaded as a texture and drawn with `squish.js`'s fragment shader, which maps each screen pixel to its position in the squished frame. The math is identical to the OBS shader; `tests/compare.js` checks it against the Python reference (worst difference 5e-13), and `tests/e2e.py` plays a squished VP9 video in headless Chromium and checks the canvas against the reference viewer (0.5/255 mean difference, landmarks within 0.02 px).

Limits: videos served cross-origin without CORS can't be read by WebGL; the extension then turns itself off for that page and logs a warning. DRM-protected video (not used by live streams) can't be read either.
