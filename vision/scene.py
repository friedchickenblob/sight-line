"""Turn one frame's detections into a scene: who the worker is, where everything is on the
floor, and the direction/distance of each hazard from the worker.

No camera or model here, so it's unit-testable (vision/tests/test_scene.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

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

    Rule: stick with the current worker's track ID; if it's gone, take the most
    confident person. (Later: a hardhat tag would make this unambiguous.)
    """

    def __init__(self):
        self.track_id: int | None = None

    def select(self, people: list[Detection]) -> Detection | None:
        if not people:
            return None
        if self.track_id is not None:
            for p in people:
                if p.track_id == self.track_id:
                    return p
        best = max(people, key=lambda p: p.conf)
        self.track_id = best.track_id
        return best


def build_scene(dets: list[Detection], floor_map: FloorMap | None = None,
                head_yaw_deg: float | None = None, selector: WorkerSelector | None = None) -> Scene:
    def place(d: Detection) -> Placed:
        xy = None if floor_map is None else tuple(float(v) for v in floor_map.to_floor([d.ground_px])[0])
        return Placed(d, xy)

    people = [d for d in dets if d.kind == "person"]
    worker_det = (selector or WorkerSelector()).select(people)
    worker = place(worker_det) if worker_det else None
    others = [place(p) for p in people if p is not worker_det]

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
