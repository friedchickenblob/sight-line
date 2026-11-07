"""Muse stream health (page 7b): rate, dropped packets, and a watchdog.

A drop is a DROP sample (the headband's packet counter skipped: exact, see
sources.PacketDecoder), or, for recordings without DROP samples, any gap over
100 ms between consecutive gyro samples. The watchdog says the stream is
stale if no gyro sample has arrived for 0.5 s; the demo dashboard shows
exactly this.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

import numpy as np

from .messages import MuseHealth

DROP_GAP_S = 0.1
STALE_S = 0.5


class StreamHealth:
    def __init__(self, drop_gap_s: float = DROP_GAP_S, stale_s: float = STALE_S, window_s: float = 60.0):
        self.drop_gap_s, self.stale_s, self.window_s = drop_gap_s, stale_s, window_s
        self.last_t: Optional[float] = None
        self.first_t: Optional[float] = None
        self._recent: deque = deque()     # sample times in the last ~1 s
        self._drops: deque = deque()      # times of drops in the last window_s
        self._explained = False           # a DROP sample already accounts for the next gap
        self.total_drops = 0
        self.lost_samples = 0
        self.total_samples = 0
        self._next_report: Optional[float] = None

    def _count_drop(self, t: float):
        self._drops.append(t)
        self.total_drops += 1

    def on_drop(self, t: float, samples_lost: float):
        self._count_drop(t)
        self.lost_samples += int(samples_lost)
        self._explained = True

    def on_sample(self, t: float):
        if self.last_t is not None and t - self.last_t > self.drop_gap_s and not self._explained:
            self._count_drop(t)
        self._explained = False
        self.first_t = t if self.first_t is None else self.first_t
        self.last_t = t
        self.total_samples += 1
        self._recent.append(t)

    def feed(self, s) -> None:
        """Convenience: route a Sample (GYRO and DROP count, the rest is ignored)."""
        if s.kind == "GYRO":
            self.on_sample(s.t)
        elif s.kind == "DROP":
            self.on_drop(s.t, s.v[0] if s.v else 0)

    def stale(self, now: float) -> bool:
        return self.last_t is None or now - self.last_t > self.stale_s

    def report(self, now: float, battery_pct: Optional[float] = None) -> MuseHealth:
        while self._recent and now - self._recent[0] > 1.0:
            self._recent.popleft()
        while self._drops and now - self._drops[0] > self.window_s:
            self._drops.popleft()
        rate = len(self._recent) / 1.0
        # before a full window has passed, divide by the time actually watched
        span = self.window_s if self.first_t is None else min(self.window_s, max(now - self.first_t, 1.0))
        return MuseHealth(now, not self.stale(now), float(rate), len(self._drops) / (span / 60.0), battery_pct)

    def maybe_report(self, now: float, every_s: float = 1.0, battery_pct: Optional[float] = None) -> Optional[MuseHealth]:
        """Call often; returns a report once per `every_s`."""
        if self._next_report is None or now >= self._next_report:
            self._next_report = now + every_s
            return self.report(now, battery_pct)
        return None


def gap_stats(t, drop_t=(), drop_n=()) -> dict:
    """Offline summary of a timestamp trace (page 3 checks, page 7 B1-B5).
    t: gyro sample times; drop_t / drop_n: times and sizes of DROP samples, if any."""
    t = np.asarray(t, float)
    drop_t, drop_n = np.asarray(drop_t, float), np.asarray(drop_n, float)
    if len(t) < 2:
        return dict(n=len(t), rate_hz=0.0, drops=len(drop_t), lost_samples=int(drop_n.sum()), drops_per_min=0.0,
                    biggest_gap_ms=0.0, minutes=0.0, disconnects=0)
    dt = np.diff(t)
    minutes = (t[-1] - t[0]) / 60.0
    gaps = np.flatnonzero(dt > DROP_GAP_S)
    # a timing gap that a DROP sample already explains is not a second drop
    explained = [np.any((drop_t > t[i]) & (drop_t <= t[i + 1])) for i in gaps] if len(drop_t) else [False] * len(gaps)
    drops = len(drop_t) + int(np.count_nonzero(~np.asarray(explained, bool)))
    return dict(n=len(t), rate_hz=float(1 / np.median(dt)), drops=drops, lost_samples=int(drop_n.sum()),
                drops_per_min=drops / minutes if minutes else 0.0,
                biggest_gap_ms=float(dt.max() * 1000), minutes=float(minutes),
                disconnects=int(np.count_nonzero(dt > 2.0)))
