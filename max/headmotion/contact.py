"""Is the machine really coming for the worker? (page 8)

A plain distance alarm fires whenever the machine is close, including when it
is just driving past. This asks: if nothing changes, how close will it get,
and how soon?

    p = machine - worker              relative position (m)
    v = v_machine - v_worker          relative velocity (m/s)
    gap(t) = |p + v t|
    t_cpa  = -(p.v)/(v.v)             time of closest approach
    ttc    = first t > 0 with |p + v t| = R   (solve the quadratic)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .frame import wrap
from .messages import Contact

STOPPED_MPS = 0.05


@dataclass(frozen=True)
class ContactGeometry:
    d: float                  # current distance, m
    ttc: Optional[float]      # seconds until the gap first reaches R; None = never (at current velocities)
    d_min: float              # closest the gap will get, m
    t_cpa: Optional[float]    # when, s (negative = already past); None if not moving
    bearing: float            # direction worker -> machine, floor frame degrees
    approaching: bool         # raw flag: ttc < T (no hysteresis)


def contact(pw, pm, vm, vw=(0.0, 0.0), R: float = 0.5, T: float = 2.0) -> ContactGeometry:
    p = np.subtract(pm, pw).astype(float)
    v = np.subtract(vm, vw).astype(float)
    d = float(np.hypot(*p))
    bearing = float(wrap(np.degrees(np.arctan2(p[1], p[0]))))   # arctan2 can give +180; the frame says [-180, 180)
    vv = float(v @ v)
    if vv < STOPPED_MPS ** 2:                       # basically not moving relative to each other
        inside = d < R
        return ContactGeometry(d, 0.0 if inside else None, d, None, bearing, inside)
    pv = float(p @ v)
    t_cpa = -pv / vv
    d_min = float(np.hypot(*(p + v * max(t_cpa, 0.0))))
    c = float(p @ p) - R * R                        # |p + v t|^2 = R^2  ->  vv t^2 + 2 pv t + c = 0
    disc = pv * pv - vv * c
    if c <= 0:
        ttc = 0.0                                   # already inside R
    elif disc >= 0:
        t1 = (-pv - np.sqrt(disc)) / vv
        ttc = float(t1) if t1 > 0 else None
    else:
        ttc = None
    return ContactGeometry(d, ttc, d_min, t_cpa, bearing, ttc is not None and ttc < T)


class ApproachFlag:
    """Hysteresis so noisy velocities don't make the alert flicker (page 8).
    On as soon as ttc < T. Off only after ttc > T + margin (or None) for hold_s."""

    def __init__(self, T: float = 2.0, off_margin_s: float = 0.5, hold_s: float = 0.3):
        self.T, self.margin, self.hold_s = T, off_margin_s, hold_s
        self.on = False
        self._clear_since: Optional[float] = None

    def update(self, t: float, ttc: Optional[float]) -> bool:
        if ttc is not None and ttc < self.T:
            self.on, self._clear_since = True, None
        elif self.on:
            clear = ttc is None or ttc > self.T + self.margin
            if not clear:
                self._clear_since = None
            else:
                if self._clear_since is None:
                    self._clear_since = t
                if t - self._clear_since >= self.hold_s:
                    self.on, self._clear_since = False, None
        return self.on


class ContactTracker:
    """contact() + hysteresis -> the published Contact message, once per camera frame."""

    def __init__(self, R: float = 0.5, T: float = 2.0, off_margin_s: float = 0.5, hold_s: float = 0.3):
        self.R, self.T = R, T
        self.flag = ApproachFlag(T, off_margin_s, hold_s)

    def update(self, t, pw, pm, vm, vw=(0.0, 0.0)) -> Contact:
        g = contact(pw, pm, vm, vw, self.R, self.T)
        on = self.flag.update(t, g.ttc)
        return Contact(t, g.d, g.ttc, g.d_min, g.bearing, on)


def estimate_velocity(ts, ps, window_s: float = 0.5, min_span_s: float = 0.3) -> np.ndarray:
    """Least-squares slope of the last `window_s` of positions (m/s). Zoe owns real
    velocities; this is for the simulator and for sanity-checking her numbers.
    With +-2 cm noise at 30 fps, a 0.5 s window gives ~0.02 m/s velocity noise;
    a 2-frame difference gives ~0.5 m/s, which is why the flag would flicker."""
    ts, ps = np.asarray(ts, float), np.asarray(ps, float)
    keep = ts >= ts[-1] - window_s
    ts, ps = ts[keep], ps[keep]
    if len(ts) < 2 or ts[-1] - ts[0] < min_span_s:
        return np.zeros(ps.shape[1])          # too little history: a 2-frame slope is mostly noise
    tc = ts - ts.mean()
    return (tc @ (ps - ps.mean(axis=0))) / float(tc @ tc)
