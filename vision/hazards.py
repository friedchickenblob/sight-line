"""Hazard geometry in the shared floor frame (see CLAUDE.md).

Positions in metres, velocities in m/s, angles in degrees, 0 deg along +x,
counter-clockwise positive seen from above, wrapped to [-180, 180).
No camera or ML here: pure maths, unit-tested in tests/test_hazards.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

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


@dataclass
class HazardGeometry:
    distance_m: float
    bearing_deg: float
    closing_speed_mps: float   # > 0 = getting closer
    t_cpa_s: float             # time until closest approach (0 if it's already receding)
    cpa_m: float               # distance at closest approach
    threat: bool


def assess(worker_xy: Vec, worker_v: Vec, hazard_xy: Vec, hazard_v: Vec,
           horizon_s: float = 3.0, radius_m: float = 1.0) -> HazardGeometry:
    """Constant-velocity closest-point-of-approach check for one hazard."""
    px, py = hazard_xy[0] - worker_xy[0], hazard_xy[1] - worker_xy[1]
    vx, vy = hazard_v[0] - worker_v[0], hazard_v[1] - worker_v[1]
    d = math.hypot(px, py)
    pv = px * vx + py * vy
    vv = vx * vx + vy * vy

    closing = -pv / d if d > 1e-9 else 0.0
    t_cpa = max(0.0, -pv / vv) if vv > 1e-12 else 0.0
    cpa = math.hypot(px + vx * t_cpa, py + vy * t_cpa)
    threat = d < radius_m or (0.0 < t_cpa < horizon_s and cpa < radius_m)
    return HazardGeometry(d, bearing_deg(worker_xy, hazard_xy), closing, t_cpa, cpa, threat)
