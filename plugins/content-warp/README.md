# Content Warp

Experimental. Takes a 16:9 picture and warps its sides by detail, so the sides are squeezed where there is little detail and the centre stays 1:1.

- **Centre width (%):** the 1:1 band down the middle, 5–30% of the width.
- **Recompute every N frames:** the warp is cached between recomputes, 2–20 frames.
- **Mode A:** the picture is written for a 4:3 canvas. Set the canvas to 4:3 in OBS Video settings and stretch the source to fill it. The filter output then stays 16:9, and OBS scales it to 4:3.
- **Mode B:** the 4:3 picture is centred in the 16:9 frame with black bars. This keeps the canvas at 16:9, so it saves no pixels. It's a control for the bitrate.

The filter itself can't change the canvas size.
