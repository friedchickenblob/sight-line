"""Keep a tagged vehicle when its ArUco tag can't be read, by following the dark blob under it.

Why: a tag on the machine is small in the image (a 6 cm tag is ~20 px wide at 1280x720 from
2.5 m), so video compression, slant and shadow often make it unreadable for seconds at a time,
even though the machine is plainly visible. When the machine is a big dark shape on a lighter
floor (the black office chair on Zoe's rig), that shape is easy to find with a threshold.

The tag still says WHICH object is the machine: the blob is only used near where the tag (or the
blob) was last seen, and only for max_gap_s after the last tag sighting. If the machine jumps too
far to follow (carried back to the start line), the biggest machine-sized blob anywhere is taken
after relock_s.

Opt-in (run_vision --blob-fallback): on a dark floor, or with a light-coloured machine, it would
follow the wrong thing. No model here: unit-tested in vision/tests/test_tracks.py.
"""

from __future__ import annotations

import cv2
import numpy as np

from vision.detector import Detection


def dark_blobs(frame: np.ndarray, v_max: int = 45, area_frac=(0.016, 0.1)):
    """Big dark regions: [(area_px, (cx, cy), (x1, y1, x2, y2))]. area_frac is the blob area as a
    share of the frame, so it doesn't depend on the resolution."""
    v = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[..., 2] if frame.ndim == 3 else frame
    mask = (v < v_max).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, _, st, cen = cv2.connectedComponentsWithStats(mask)
    lo, hi = (f * mask.size for f in area_frac)
    out = []
    for i in range(1, n):
        a = st[i, cv2.CC_STAT_AREA]
        if lo <= a <= hi:
            x, y, w, h = st[i, :4]
            out.append((int(a), (float(cen[i][0]), float(cen[i][1])), (float(x), float(y), float(x + w), float(y + h))))
    return out


class BlobFallback:
    def __init__(self, v_max: int = 45, area_frac=(0.016, 0.1), max_jump_px: float = 200.0,
                 relock_s: float = 1.0, max_gap_s: float = 30.0):
        self.v_max, self.area_frac = v_max, area_frac
        self.max_jump_px, self.relock_s, self.max_gap_s = max_jump_px, relock_s, max_gap_s
        self._state: dict[int, dict] = {}   # tag id -> last px, last time seen (any way), last tag time, offset

    def update(self, t: float, frame: np.ndarray, found: dict[int, np.ndarray],
               labels: dict[int, str]) -> list[Detection]:
        """found: tags seen this frame {id: 4x2 corners}; labels: every vehicle tag id -> label.
        Returns a "blob" vehicle detection for each tag that isn't seen now but was recently."""
        blobs = None
        out = []
        for mid, lbl in labels.items():
            s = self._state.get(mid)
            if mid in found:
                px = np.asarray(found[mid], dtype=float).mean(axis=0)
                blobs = dark_blobs(frame, self.v_max, self.area_frac) if blobs is None else blobs
                b = self._nearest(blobs, px)
                offset = (px - b[1]) if b is not None else (s["offset"] if s else np.zeros(2))
                self._state[mid] = {"px": px, "t": t, "tag_t": t, "offset": offset}
                continue
            if s is None or t - s["tag_t"] > self.max_gap_s:
                continue
            blobs = dark_blobs(frame, self.v_max, self.area_frac) if blobs is None else blobs
            lost = t - s["t"] > self.relock_s
            b = max(blobs, default=None) if lost else self._nearest(blobs, s["px"])
            if b is None:
                continue
            px = np.asarray(b[1]) + s["offset"]
            s["px"], s["t"] = px, t
            out.append(Detection("vehicle", lbl, 0.5, b[2], mid, ground=(float(px[0]), float(px[1])), source="blob"))
        return out

    def _nearest(self, blobs, px):
        best = min(blobs, key=lambda b: np.hypot(b[1][0] - px[0], b[1][1] - px[1]), default=None)
        if best is None or np.hypot(best[1][0] - px[0], best[1][1] - px[1]) > self.max_jump_px:
            return None
        return best
