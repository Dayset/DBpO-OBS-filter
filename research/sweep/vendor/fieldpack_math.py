"""Field-pack ("interlace step") reference math, shared by the bench, the OBS filter test and the viewer test.

Streamer side, per output frame n with parity p = n & 1 (0 = even source rows, 1 = odd rows):
    optional vertical anti-twitter pre-filter, then field row y = source row 2y + p,
    written to the top H/2 rows of the frame; the rest is black (the starvation marker sits there).

Viewer side (motion-adaptive, no look-ahead, yadif-style without the future field):
    received rows are copied; for a missing row r:
        c, e  = received rows above / below                      (current field, parity p)
        T     = row r of the previous field                      (parity 1-p, one frame old)
        diff  = max(|c - c2|, |e - e2|) * sensitivity             (c2, e2: same rows two frames old)
        S     = edge-directed interpolation of c and e (3-tap ELA)
        out   = clamp(S, T - diff, T + diff)
    Static: diff = 0 -> the true row from the previous field (full 1080 detail).
    Moving: diff large -> the spatial estimate. No threshold, so no flicker at a threshold edge.
"""
import numpy as np

ANTI_TWITTER = {
    0: None,
    1: np.array([1, 2, 1], np.float32) / 4,
    2: np.array([1, 6, 1], np.float32) / 8,
    3: np.array([1, 2, 2, 2, 1], np.float32) / 8,
}


def prefilter(img, level):
    """Vertical low-pass on an HxWxC uint8 image (edge rows clamped)."""
    k = ANTI_TWITTER.get(int(level))
    if k is None:
        return img
    r = len(k) // 2
    f = img.astype(np.float32)
    pad = np.concatenate([f[:1].repeat(r, 0), f, f[-1:].repeat(r, 0)], 0)
    out = sum(k[i] * pad[i:i + f.shape[0]] for i in range(len(k)))
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def pack(img, parity, level=1):
    """HxWxC source -> HxWxC packed frame (field in the top half, black below)."""
    h = img.shape[0]
    src = prefilter(img, level)
    out = np.zeros_like(img)
    out[:h // 2] = src[parity::2][:h // 2]
    return out


def pack_split(img, parity, prev_packed=None, level=1):
    """Split packing: even fields live in the top half, odd fields in the bottom half. Each frame replaces
    only its own half; the other half is the previous field, unchanged, so the encoder can predict every
    field from the last field of the same parity (no half-line jitter) and skips the static half."""
    h = img.shape[0]
    out = np.zeros_like(img) if prev_packed is None else prev_packed.copy()
    src = prefilter(img, level)
    out[parity * (h // 2):(parity + 1) * (h // 2)] = src[parity::2][:h // 2]
    return out


def view_split(packed, parity, viewer):
    """Split-packed frame -> full frame. The newest field is the half named by the parity."""
    h = packed.shape[0] // 2
    return viewer.feed(packed[parity * h:(parity + 1) * h], parity)


class Viewer:
    """Stateful deinterlacer. feed(field, parity) -> full frame. field: (H/2)xWxC uint8."""

    def __init__(self, sensitivity=1.0, ela=True):
        self.sens = float(sensitivity)
        self.ela = ela
        self.f1 = None  # previous field (other parity)
        self.f2 = None  # field two frames ago (same parity)
        self.p1 = None

    @staticmethod
    def _neighbours(field, parity):
        """For missing rows of a frame whose received rows have `parity`: rows above (c) and below (e)."""
        # missing row r = 2y + (1-p). parity 0: missing 2y+1, above = field[y], below = field[y+1]
        # parity 1: missing 2y,   above = field[y-1], below = field[y]
        if parity == 0:
            c = field
            e = np.concatenate([field[1:], field[-1:]], 0)
        else:
            c = np.concatenate([field[:1], field[:-1]], 0)
            e = field
        return c, e

    def _spatial(self, c, e):
        if not self.ela:
            return (c + e) * 0.5
        # 3-tap edge-directed line average on luma-ish sum, per pixel pick the direction with the smallest diff
        def sh(a, d):
            if d == 0:
                return a
            return np.concatenate([a[:, d:], a[:, -1:].repeat(d, 1)], 1) if d > 0 else \
                np.concatenate([a[:, :1].repeat(-d, 1), a[:, :d]], 1)
        best = None
        best_cost = None
        for d in (0, -1, 1):
            ca, eb = sh(c, d), sh(e, -d)
            cost = np.abs(ca - eb).sum(-1)
            if d != 0:
                cost = cost + 6.0  # prefer vertical unless a diagonal is clearly better
            if best is None:
                best, best_cost = (ca + eb) * 0.5, cost
            else:
                m = cost < best_cost
                best = np.where(m[..., None], (ca + eb) * 0.5, best)
                best_cost = np.where(m, cost, best_cost)
        return best

    def feed(self, field, parity):
        f = field.astype(np.float32)
        h2, w, ch = f.shape
        out = np.empty((h2 * 2, w, ch), np.float32)
        out[parity::2] = f
        c, e = self._neighbours(f, parity)
        S = self._spatial(c, e)
        if self.f1 is not None and self.p1 == 1 - parity and self.f2 is not None:
            T = self.f1
            c2, e2 = self._neighbours(self.f2, parity)
            diff = np.maximum(np.abs(c - c2), np.abs(e - e2)).max(-1, keepdims=True) * self.sens
            miss = np.clip(S, T - diff, T + diff)
        else:
            miss = S
        out[1 - parity::2] = miss
        self.f2, self.f1, self.p1 = self.f1, f, parity
        return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def view(packed, parity, viewer):
    """Packed HxWxC frame -> full frame through a Viewer."""
    h = packed.shape[0]
    return viewer.feed(packed[:h // 2], parity)
