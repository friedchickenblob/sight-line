"""Scene building (worker choice, floor placement, hazard angles) with fake detections seen by the
simulated angled camera from test_floor.py, plus one real YOLO run on a sample image."""

import numpy as np
import pytest

from test_floor import LAYOUT, project, render
from vision.detector import Detection, kind_of
from vision.floor import calibrate
from vision.scene import WorkerSelector, build_scene


@pytest.fixture(scope="module")
def fm():
    return calibrate(render(), LAYOUT)[0]


def fake(kind, floor_xy, conf=0.9, track_id=None, label=None, w=40, h=120):
    """A detection whose bottom-centre lands on floor_xy in the simulated camera."""
    u, v = project([[floor_xy[0], floor_xy[1], 0.0]])[0]
    return Detection(kind, label or ("person" if kind == "person" else "car"), conf,
                     (u - w / 2, v - h, u + w / 2, v), track_id)


def test_kind_mapping():
    assert kind_of("person") == "person"
    assert kind_of("truck") == "vehicle" and kind_of("bus") == "vehicle"
    assert kind_of("dog") is None
    assert kind_of("truck", vehicle_labels={"car"}) is None


def test_hazard_distance_and_bearing_on_floor(fm):
    s = build_scene([fake("person", (1.0, 1.0), track_id=1), fake("vehicle", (2.0, 2.0), track_id=7)], fm)
    assert np.allclose(s.worker.floor_xy, (1.0, 1.0), atol=0.03)
    (r,) = s.hazards
    assert r.distance_m == pytest.approx(np.sqrt(2), abs=0.04)
    assert r.bearing_deg == pytest.approx(45, abs=2)
    assert r.rel_bearing_deg is None                     # no head yaw given


def test_relative_bearing_with_head_yaw(fm):
    dets = [fake("person", (1.0, 1.0)), fake("vehicle", (0.0, 1.0))]   # car toward -x
    r = build_scene(dets, fm, head_yaw_deg=90).hazards[0]               # worker faces +y
    assert r.rel_bearing_deg == pytest.approx(90, abs=2)                # car is on their left


def test_hazards_sorted_nearest_first(fm):
    dets = [fake("person", (1.5, 1.0)), fake("vehicle", (2.9, 1.9)), fake("vehicle", (2.0, 1.0))]
    d = [r.distance_m for r in build_scene(dets, fm).hazards]
    assert d == sorted(d)


def test_no_worker_or_no_floor_map_gives_no_angles(fm):
    no_worker = build_scene([fake("vehicle", (2.0, 1.0))], fm)
    assert no_worker.worker is None and no_worker.hazards[0].bearing_deg is None
    no_map = build_scene([fake("person", (1.0, 1.0)), fake("vehicle", (2.0, 1.0))], None)
    assert no_map.worker.floor_xy is None and no_map.hazards[0].distance_m is None


def test_worker_selection_is_sticky():
    sel = WorkerSelector()
    a = Detection("person", "person", 0.9, (0, 0, 1, 1), track_id=1)
    b = Detection("person", "person", 0.6, (0, 0, 1, 1), track_id=2)
    assert sel.select([a, b]).track_id == 1
    a2 = Detection("person", "person", 0.5, (0, 0, 1, 1), track_id=1)
    b2 = Detection("person", "person", 0.95, (0, 0, 1, 1), track_id=2)
    assert sel.select([a2, b2]).track_id == 1            # keeps worker even if someone is more confident
    assert sel.select([b2]).track_id == 2                # worker gone -> best remaining person
    assert sel.select([]) is None


def tag(cx, cy, half=10):
    """Corner pixels of a marker centred on (cx, cy)."""
    return np.array([[cx - half, cy - half], [cx + half, cy - half], [cx + half, cy + half], [cx - half, cy + half]])


def test_hardhat_marker_picks_the_worker_and_fixes_its_id():
    sel = WorkerSelector(hardhat_marker=10)
    worker = Detection("person", "person", 0.4, (100, 100, 200, 300), track_id=5)
    other = Detection("person", "person", 0.95, (400, 100, 500, 300), track_id=6)
    w = sel.select([worker, other], {10: tag(150, 150)})
    assert w.track_id == 10 and w.box == worker.box      # less confident, but wearing the hardhat
    assert w.ground_px == (150, 150) and w.source == "marker"
    renumbered = Detection("person", "person", 0.4, (100, 100, 200, 300), track_id=9)
    assert sel.select([renumbered, other], {10: tag(152, 150)}).track_id == 10   # ByteTrack new id: same worker


def test_hardhat_marker_without_a_person_box_is_the_worker():
    w = WorkerSelector(hardhat_marker=10).select([], {10: tag(300, 200)})
    assert w is not None and w.track_id == 10 and w.ground_px == (300, 200)


def test_hardhat_hidden_keeps_the_nearest_person_and_ignores_far_ones():
    sel = WorkerSelector(hardhat_marker=10, max_jump_px=150)
    assert sel.select([Detection("person", "person", 0.9, (400, 100, 500, 300), 6)], {}) is None  # no marker seen yet
    sel.select([], {10: tag(150, 200)})
    near = Detection("person", "person", 0.3, (110, 120, 210, 300), track_id=7)
    far = Detection("person", "person", 0.99, (600, 100, 700, 300), track_id=8)
    assert sel.select([near, far], {}).box == near.box   # marker hidden (crouching): nearest person
    assert sel.select([far], {}) is None                 # only a far-away person: not the worker


def test_hardhat_worker_is_not_listed_as_another_person(fm):
    p = fake("person", (1.0, 1.0), track_id=3)
    cx, cy = (p.box[0] + p.box[2]) / 2, p.box[1] + 10
    s = build_scene([p, fake("person", (2.5, 1.5), track_id=4)], fm, selector=WorkerSelector(10),
                    markers={10: tag(cx, cy, 5)})
    assert s.worker.det.track_id == 10
    assert [o.det.track_id for o in s.other_people] == [4]


def test_yolo_detects_person_and_vehicle_on_sample_image():
    pytest.importorskip("ultralytics")
    import cv2
    from ultralytics.utils import ASSETS

    from vision.detector import Detector

    img = cv2.imread(str(ASSETS / "bus.jpg"))
    dets = Detector("yolo26n.pt", track=False)(img)
    kinds = {d.kind for d in dets}
    assert kinds == {"person", "vehicle"}
    assert any(d.label == "bus" for d in dets)


def test_raised_machine_tag_distance_uses_its_floor_point(fm):
    """build_scene with tag heights: a tag 0.8 m up on the machine is placed where the machine stands."""
    from test_floor import CAM
    from vision.floor import TagHeights

    worker = fake("person", (1.0, 1.0), track_id=1)
    px = project([[2.0, 1.0, 0.8]])[0]                       # tag on top of a machine standing at (2.0, 1.0)
    tag = Detection("vehicle", "chair", 1.0, (px[0] - 5, px[1] - 5, px[0] + 5, px[1] + 5), 20,
                    ground=(float(px[0]), float(px[1])), source="marker")
    raw = build_scene([worker, tag], fm).hazards[0]
    th = TagHeights(float(CAM[2]), (float(CAM[0]), float(CAM[1])), {20: 0.8})
    fixed = build_scene([worker, tag], fm, tag_heights=th).hazards[0]
    assert abs(raw.distance_m - 1.0) > 0.2                    # uncorrected: wrong by tens of cm
    assert fixed.distance_m == pytest.approx(1.0, abs=0.03)
    assert fixed.bearing_deg == pytest.approx(0, abs=2)
