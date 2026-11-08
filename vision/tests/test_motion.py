"""Car motion through the whole pipeline: the simulated camera films a tagged toy car driving across the floor.

Alert rule: the car is heading at the worker (its direction of travel within the cone) AND the worker isn't
facing it. Checked for each combination, plus a parked car.
"""

import pytest

from test_floor import LAYOUT, render
from vision.detector import Detection
from vision.pipeline import VisionPipeline, scene_to_dict

WORKER_BOX = (300, 200, 360, 420)          # feet land near floor (0.10, 0.54) in the simulated camera


class FakeDetector:
    weights = "fake"

    def __call__(self, frame):
        return [Detection("person", "person", 0.9, WORKER_BOX, 1)]


def drive(monkeypatch, path, head_yaw, fps=15.0):
    """Feed one frame per point of `path` (floor metres) and return the last hazard message."""
    pipe = VisionPipeline(FakeDetector(), LAYOUT, head_yaw_deg=head_yaw, hazard_markers={42: "toy car"}, tags_only=True)
    msg = None
    for i, xy in enumerate(path):
        res = pipe.process(render(extra={42: xy}), t_s=i / fps)   # capture time drives the velocity
        msg = scene_to_dict(res, pipe.head_yaw_deg, pipe.facing_deg, pipe=pipe)
    return msg["hazards"][0], pipe


def line(start, end, n=12):
    return [(start[0] + (end[0] - start[0]) * i / (n - 1), start[1] + (end[1] - start[1]) * i / (n - 1)) for i in range(n)]


def test_car_driving_at_worker_who_looks_away_alerts(monkeypatch):
    h, _ = drive(monkeypatch, line((2.8, 0.5), (2.3, 0.5)), head_yaw=90)     # car heads -x toward the worker
    assert h["speed_mps"] == pytest.approx(0.68, abs=0.15)
    assert abs(h["travel_deg"]) == pytest.approx(180, abs=10)
    assert abs(h["approach_deg"]) < 10
    assert h["approaching"] is True and h["facing"] is False and h["alert"] is True


def test_car_driving_at_worker_who_watches_it_does_not_alert(monkeypatch):
    h, _ = drive(monkeypatch, line((2.8, 0.5), (2.3, 0.5)), head_yaw=0)      # worker looks along +x, at the car
    assert h["approaching"] is True and h["facing"] is True and h["alert"] is False


def test_car_crossing_does_not_alert_even_unwatched(monkeypatch):
    h, _ = drive(monkeypatch, line((2.5, 0.2), (2.5, 0.9)), head_yaw=90)     # car drives +y, across
    assert abs(h["approach_deg"]) == pytest.approx(90, abs=15)
    assert h["approaching"] is False and h["alert"] is False


def test_parked_car_never_alerts(monkeypatch):
    h, _ = drive(monkeypatch, [(2.5, 0.5)] * 12, head_yaw=90)
    assert h["speed_mps"] < 0.1 and h["travel_deg"] is None
    assert h["approaching"] is False and h["alert"] is False


def test_wider_cone_catches_a_glancing_approach(monkeypatch):
    path = line((2.8, 1.0), (2.3, 0.75))                                     # ~25 deg off a direct line
    h, pipe = drive(monkeypatch, path, head_yaw=90)
    assert 15 < abs(h["approach_deg"]) < 40
    pipe.approach_cone_deg = 10
    assert pipe.approaching(type("R", (), {"speed_mps": h["speed_mps"], "distance_m": 1, "approach_deg": h["approach_deg"]})) is False
    pipe.approach_cone_deg = 45
    assert pipe.approaching(type("R", (), {"speed_mps": h["speed_mps"], "distance_m": 1, "approach_deg": h["approach_deg"]})) is True
