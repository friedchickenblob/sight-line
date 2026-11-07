"""The shared floor frame (same for Max, Hanh and Zoe).

Origin at floor marker 0, +x toward marker 1, +y toward marker 3.
Yaw in degrees, 0 along +x, counter-clockwise positive seen from above
(turning your head left increases yaw), wrapped to [-180, 180).
Positions in metres, velocities in m/s, times from pylsl.local_clock().
"""
from __future__ import annotations

import numpy as np


def wrap(a):
    """Any angle in degrees (scalar or array) -> [-180, 180)."""
    if np.ndim(a):
        return (np.asarray(a, dtype=float) + 180.0) % 360.0 - 180.0
    return (float(a) + 180.0) % 360.0 - 180.0


def angle_diff(a, b):
    """Signed smallest difference a - b in degrees, in [-180, 180)."""
    return wrap(np.subtract(a, b))


def unwrap(deg):
    """Remove 360 jumps from a yaw trace so it can be plotted or differentiated."""
    return np.degrees(np.unwrap(np.radians(np.asarray(deg, dtype=float))))
