"""Turn one frame's detections into a scene: who the worker is, where everything is on the
floor, and the direction/distance of each hazard from the worker.

No camera or model here, so it's unit-testable (vision/tests/test_scene.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

from vision.detector import Detection
from vision.floor import FloorMap
from vision.hazards import bearing_deg, distance_m, relative_bearing_deg


@dataclass
class Placed:
    det: Detection
    floor_xy: tuple[float, float] | None       # None when there's no floor calibration


@dataclass
class HazardReading:
    hazard: Placed
    distance_m: float | None = None
    bearing_deg: float | None = None           # floor frame: 0 = +x, CCW +
    rel_bearing_deg: float | None = None       # relative to head yaw: 0 ahead, +90 left


@dataclass
class Scene:
    worker: Placed | None
    hazards: list[HazardReading] = field(default_factory=list)
    other_people: list[Placed] = field(default_factory=list)


class WorkerSelector:
    """Pick which detected person is the worker, and keep the same one while it's tracked.

    Without a hardhat marker: stick with the current worker's track ID; if it's gone, take the
    most confident person.

    With hardhat_marker (an ArUco id taped on top of the worker's hardhat): the person whose box
    contains that marker is the worker. If YOLO has no box there (top-down people are often
    missed), the marker itself becomes the worker. Between marker sightings, the person box
    nearest the last worker position is kept. The worker's track_id is then always the marker id,
    so it doesn't change when ByteTrack re-numbers the person.
    """

    def __init__(self, hardhat_marker: int | None = None, max_jump_px: float = 150.0):
        self.track_id: int | None = None
        self.hardhat_marker = hardhat_marker
        self.max_jump_px = max_jump_px
        self.last_px: tuple[float, float] | None = None

    def select(self, people: list[Detection], markers: dict | None = None) -> Detection | None:
        if self.hardhat_marker is not None:
            return self._select_hardhat(people, (markers or {}).get(self.hardhat_marker))
        if not people:
            return None
        if self.track_id is not None:
            for p in people:
                if p.track_id == self.track_id:
                    return p
        best = max(people, key=lambda p: p.conf)
        self.track_id = best.track_id
        return best

    def _select_hardhat(self, people: list[Detection], corners) -> Detection | None:
        mid = self.hardhat_marker
        if corners is not None:
            c = np.asarray(corners, dtype=float)
            cx, cy = (float(v) for v in c.mean(axis=0))
            self.last_px = (cx, cy)
            inside = [p for p in people if p.box[0] <= cx <= p.box[2] and p.box[1] <= cy <= p.box[3]]
            if inside:   # keep YOLO's box (for drawing), but the marker is the floor point
                p = max(inside, key=lambda p: p.conf)
                return replace(p, track_id=mid, ground=(cx, cy), source="marker")
            (x1, y1), (x2, y2) = c.min(axis=0), c.max(axis=0)
            return Detection("person", "worker (hardhat)", 1.0, (float(x1), float(y1), float(x2), float(y2)),
                             mid, ground=(cx, cy), source="marker")
        if not people or self.last_px is None:
            return None   # no marker yet: don't guess which person is the worker
        def centre(p):
            return (p.box[0] + p.box[2]) / 2, (p.box[1] + p.box[3]) / 2
        near = min(people, key=lambda p: math.dist(centre(p), self.last_px))
        if math.dist(centre(near), self.last_px) > self.max_jump_px:
            return None   # nearest person is too far away: probably someone else
        self.last_px = centre(near)
        return replace(near, track_id=mid)


def build_scene(dets: list[Detection], floor_map: FloorMap | None = None,
                head_yaw_deg: float | None = None, selector: WorkerSelector | None = None,
                markers: dict | None = None) -> Scene:
    """markers: {ArUco id: 4x2 corner pixels} seen this frame, used by a hardhat WorkerSelector."""
    def place(d: Detection) -> Placed:
        xy = None if floor_map is None else tuple(float(v) for v in floor_map.to_floor([d.ground_px])[0])
        return Placed(d, xy)

    people = [d for d in dets if d.kind == "person"]
    worker_det = (selector or WorkerSelector()).select(people, markers)
    worker = place(worker_det) if worker_det else None
    others = [place(p) for p in people
              if worker_det is None or p.box != worker_det.box]   # the worker may be a copy

    readings = []
    for v in (d for d in dets if d.kind == "vehicle"):
        h = place(v)
        r = HazardReading(h)
        if worker and worker.floor_xy and h.floor_xy:
            r.distance_m = distance_m(worker.floor_xy, h.floor_xy)
            r.bearing_deg = bearing_deg(worker.floor_xy, h.floor_xy)
            if head_yaw_deg is not None:
                r.rel_bearing_deg = relative_bearing_deg(r.bearing_deg, head_yaw_deg)
        readings.append(r)
    readings.sort(key=lambda r: (r.distance_m is None, r.distance_m or 0.0))
    return Scene(worker, readings, others)
