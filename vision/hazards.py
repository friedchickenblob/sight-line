"""Hazard geometry in the shared floor frame (see CLAUDE.md).

Positions in metres, angles in degrees, 0 deg along +x,
counter-clockwise positive seen from above, wrapped to [-180, 180).
No camera or ML here: pure maths, unit-tested in vision/tests/test_hazards.py.

Time to contact (closest approach + hysteresis) is not here: max/headmotion/contact.py
(contact(), ContactTracker -> sightline/contact) is the one implementation for the repo.
Vision supplies the worker and machine positions and velocities it needs.
"""

from __future__ import annotations

import math

Vec = tuple[float, float]


def wrap180(deg: float) -> float:
    """Any angle -> [-180, 180)."""
    return (deg + 180.0) % 360.0 - 180.0


def bearing_deg(from_xy: Vec, to_xy: Vec) -> float:
    """Direction from one floor point to another (0 = +x, CCW positive)."""
    dx, dy = to_xy[0] - from_xy[0], to_xy[1] - from_xy[1]
    return wrap180(math.degrees(math.atan2(dy, dx)))


def distance_m(a: Vec, b: Vec) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def relative_bearing_deg(bearing: float, head_yaw: float) -> float:
    """Direction relative to where the head points: 0 ahead, +90 left, -90 right, -180 behind."""
    return wrap180(bearing - head_yaw)


def in_view(rel_bearing: float, fov_deg: float = 120.0) -> bool:
    """True if a direction is inside a field of view centred on the head."""
    return abs(rel_bearing) <= fov_deg / 2.0
