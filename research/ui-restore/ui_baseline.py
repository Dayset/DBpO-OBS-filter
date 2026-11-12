"""UI.2 baseline: per-element PSNR/SSIM after a starved CBR stream (issue #32).

Source: bitrate_harness.clip_frames (960x540, HUD from hud()). Arms:
  full  = 960x540 x264 CBR -> decode
  360p  = 640x360 x264 CBR -> decode -> bicubic up to 960x540
each with bicubic and bicubic + unsharp (sigma 1.5, amount 1.0).
Element boxes are the HUD regions in bitrate_harness.hud(): health bar, minimap, text strip.
"""
import sys, subprocess, json
import numpy as np, cv2
from skimage.metrics import structural_similarity as ssim
sys.path.insert(0, "plugins/perceptual-color-reduction/tests")
import bitrate_harness as bh

W, H, N = 960, 540, 30
BOX = {"health": (488, 512, 40, 250), "minimap": (20, 200, 20, 200), "text": (30, 42, 660, 924)}

def cbr_roundtrip(frames_u8, w, h, kbps):
    raw = np.ascontiguousarray(frames_u8).tobytes()
    enc = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", "60",
        "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k",
        "-bufsize", f"{kbps*2}k", "-g", "120", "-pix_fmt", "yuv420p", "-f", "matroska", "-"],
        input=raw, capture_output=True, check=True).stdout
    dec = subprocess.run(["ffmpeg", "-v", "error", "-i", "-", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        input=enc, capture_output=True, check=True).stdout
    return np.frombuffer(dec, np.uint8).reshape(-1, h, w, 3)

def unsharp(img):
    return np.clip(img + (img - cv2.GaussianBlur(img, (0, 0), 1.5)), 0, 1)

def score(out, gt, box):
    y0, y1, x0, x1 = box
    a, b = out[:, y0:y1, x0:x1], gt[:, y0:y1, x0:x1]
    mse = np.mean((a - b) ** 2, axis=(1, 2, 3))
    psnr = float(np.mean(10 * np.log10(1 / np.maximum(mse, 1e-10))))
    s = float(np.mean([ssim(p, q, channel_axis=2, data_range=1.0) for p, q in zip(a, b)]))
    return round(psnr, 2), round(s, 4)

if __name__ == "__main__":
    gt = np.stack(list(bh.clip_frames("fps_pan", N))).astype(np.float32)
    gt8 = (gt * 255 + 0.5).astype(np.uint8)
    res = {}
    for kbps in (250, 300):
        arms = {}
        full = cbr_roundtrip(gt8, W, H, kbps).astype(np.float32) / 255
        small = np.stack([cv2.resize(f, (640, 360), interpolation=cv2.INTER_AREA) for f in gt8])
        low = cbr_roundtrip(small, 640, 360, kbps).astype(np.float32) / 255
        up = np.stack([cv2.resize(f, (W, H), interpolation=cv2.INTER_CUBIC) for f in low])
        for name, img in {"full_540": full, "360p_bicubic": up, "360p_bicubic_unsharp": np.stack([unsharp(f) for f in up])}.items():
            arms[name] = {el: score(img, gt, b) for el, b in BOX.items()}
        res[f"{kbps}kbps"] = arms
    print(json.dumps(res, indent=1))
    json.dump(res, open("research/ui-restore/ui_baseline.json", "w"), indent=1)
