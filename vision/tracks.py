"""Per-track velocity in the floor frame, for max/headmotion/contact.py.

Each track (the worker, each vehicle) keeps its last window_s of floor positions; the velocity is
the slope of a straight-line fit through them, which smooths the frame-to-frame jitter of a box or
a marker centre. Times are capture times in seconds (pylsl.local_clock() live, video time for a
file), so a slow model doesn't change the speed.

No camera or model here: unit-tested in vision/tests/test_tracks.py.
"""

from __future__ import annotations

from collections import deque

import numpy as np


class TrackVelocity:
    def __init__(self, window_s: float = 0.5, min_points: int = 5, min_span_s: float = 0.2,
                 forget_s: float = 1.0):
        """forget_s: a track unseen this long starts again (a re-found car shouldn't inherit a
        velocity from where it was a second ago)."""
        self.window_s, self.min_points, self.min_span_s, self.forget_s = window_s, min_points, min_span_s, forget_s
        self._hist: dict[object, deque] = {}

    def update(self, key, t: float, xy: tuple[float, float]) -> tuple[float, float] | None:
        """Add one floor position (metres) at time t (s); return (vx, vy) in m/s, or None while
        there isn't enough history yet."""
        h = self._hist.get(key)
        if h is None or (h and t - h[-1][0] > self.forget_s):
            h = self._hist[key] = deque()
        h.append((t, float(xy[0]), float(xy[1])))
        while h and t - h[0][0] > self.window_s:
            h.popleft()
        if len(h) < self.min_points or h[-1][0] - h[0][0] < self.min_span_s:
            return None
        a = np.array(h)
        tt = a[:, 0] - a[0, 0]
        return float(np.polyfit(tt, a[:, 1], 1)[0]), float(np.polyfit(tt, a[:, 2], 1)[0])
