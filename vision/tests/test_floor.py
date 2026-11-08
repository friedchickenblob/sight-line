"""Floor mapping with a simulated ANGLED camera: render the markers on a virtual floor, photograph
them with a pinhole camera 1.8 m up looking down at the floor, then check that detection +
calibration recover true floor positions."""

import cv2
import numpy as np
import pytest

from vision.floor import FloorLayout, FloorMap, TagHeights, calibrate, ground_point, lift_to_floor

LAYOUT = FloorLayout("DICT_4X4_50", 0.25, {0: (0.0, 0.0), 1: (3.0, 0.0), 2: (3.0, 2.0), 3: (0.0, 2.0)})
IMG_W, IMG_H, F = 1280, 720, 600.0   # ~94 deg horizontal FOV, typical wide webcam
K = np.array([[F, 0, IMG_W / 2], [0, F, IMG_H / 2], [0, 0, 1]])
CAM = np.array([1.5, -1.5, 1.8])          # camera position (m): behind marker 0-1 edge, 1.8 m up
LOOK = np.array([1.5, 1.0, 0.0])          # aimed at the middle of the floor -> ~36 deg below horizontal


def _rotation():
    f = (LOOK - CAM) / np.linalg.norm(LOOK - CAM)
    r = np.cross(f, [0, 0, 1.0]); r /= np.linalg.norm(r)
    d = np.cross(f, r)
    return np.vstack([r, d, f])            # rows: camera x (right), y (down), z (forward)


R = _rotation()


def project(xyz):
    """True camera projection of 3-D world points (N,3) -> pixels (N,2)."""
    pc = (R @ (np.asarray(xyz, float) - CAM).T).T
    return (K @ (pc / pc[:, 2:3]).T).T[:, :2]


def render(rotate_marker: int | None = None, extra: dict | None = None):
    """The floor seen by the angled camera; `extra` adds markers {id: (x, y)} (e.g. a tag on a car)."""
    S, ox, oy = 400, 0.5, 2.5               # canvas: 400 px/m covering x -0.5..3.5, y -0.5..2.5
    canvas = np.full((int(3.0 * S), int(4.0 * S)), 255, np.uint8)
    dic = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    side = int(LAYOUT.marker_size_m * S)
    for mid, (cx, cy) in {**LAYOUT.markers, **(extra or {})}.items():
        m = cv2.aruco.generateImageMarker(dic, mid, side)
        if mid == rotate_marker:
            m = np.rot90(m).copy()
        u, v = int(round((cx + ox) * S - side / 2)), int(round((oy - cy) * S - side / 2))
        canvas[v:v + side, u:u + side] = m
    A = np.array([[1 / S, 0, -ox], [0, -1 / S, oy], [0, 0, 1]])        # canvas px -> floor m
    t = -R @ CAM
    H_floor_to_cam = K @ np.column_stack([R[:, 0], R[:, 1], t])
    return cv2.warpPerspective(canvas, H_floor_to_cam @ A, (IMG_W, IMG_H), borderValue=128)


def test_camera_really_is_angled():
    tilt = np.degrees(np.arcsin(-R[2, 2]))
    assert 25 < tilt < 60


def test_calibrate_from_angled_view_recovers_floor_positions():
    fm, missing = calibrate(render(), LAYOUT)
    assert missing == []
    assert max(fm.errors_m.values()) < 0.01                      # corners within 1 cm
    rng = np.random.default_rng(0)
    pts = np.c_[rng.uniform(0, 3, 50), rng.uniform(0, 2, 50), np.zeros(50)]
    est = fm.to_floor(project(pts))
    assert np.abs(est - pts[:, :2]).max() < 0.02                 # anywhere on the floor within 2 cm


def test_round_trip_and_save_load(tmp_path):
    fm, _ = calibrate(render(), LAYOUT)
    floor = np.array([[0.5, 0.5], [2.5, 1.5]])
    assert np.allclose(fm.to_floor(fm.to_pixel(floor)), floor, atol=1e-9)
    fm.save(tmp_path / "calib.json")
    assert np.allclose(FloorMap.load(tmp_path / "calib.json").H, fm.H)


def test_head_height_point_lands_far_behind_the_person():
    """Why we use feet: a head 1.7 m up maps to the wrong floor spot with an angled camera."""
    fm, _ = calibrate(render(), LAYOUT)
    feet, head = project([[1.5, 1.0, 0.0], [1.5, 1.0, 1.7]])
    assert np.linalg.norm(fm.to_floor([feet])[0] - [1.5, 1.0]) < 0.02
    assert np.linalg.norm(fm.to_floor([head])[0] - [1.5, 1.0]) > 1.0


def test_rotated_marker_shows_large_error():
    fm, _ = calibrate(render(rotate_marker=1), LAYOUT)
    assert fm.errors_m[1] > 0.05 and fm.errors_m[1] == max(fm.errors_m.values())


def test_ground_point_is_bottom_centre():
    assert ground_point((100, 50, 140, 210)) == (120.0, 210.0)


def test_too_few_markers_raises():
    with pytest.raises(ValueError):
        calibrate(np.full((IMG_H, IMG_W), 255, np.uint8), LAYOUT)


# --------------------------------------------------------------------------- raised tags
NADIR = (float(CAM[0]), float(CAM[1]))      # floor point straight under the simulated lens


def test_lift_to_floor_formula():
    # mapped 1 m from the nadir, tag 0.8 m up, camera 2.5 m up -> really 1 * 1.7 / 2.5 = 0.68 m away
    assert lift_to_floor((2.0, 0.5), 0.8, 2.5, (1.0, 0.5)) == pytest.approx((1.68, 0.5))
    assert lift_to_floor((2.0, 0.5), 0.0, 2.5, (1.0, 0.5)) == pytest.approx((2.0, 0.5))   # on the floor: unchanged
    with pytest.raises(ValueError):
        lift_to_floor((2.0, 0.5), 2.5, 2.5, (1.0, 0.5))                                   # at the lens: impossible


@pytest.mark.parametrize("xy,h", [((1.2, 1.0), 0.8), ((2.0, 0.5), 1.6), ((0.4, 1.8), 1.2)])
def test_raised_tag_is_moved_back_onto_its_floor_point(xy, h):
    """Photograph a tag at height h with the angled camera, map it, correct it: lands on its floor point."""
    from vision.detector import Detection

    fm = calibrate(render(), LAYOUT)[0]
    px = project([[xy[0], xy[1], h]])[0]
    mapped = fm.to_floor([px])[0]
    assert np.hypot(*(mapped - np.array(xy))) > 0.3                 # uncorrected: tens of cm off
    th = TagHeights(float(CAM[2]), NADIR, {20: h})
    tag = Detection("vehicle", "chair", 1.0, (0, 0, 1, 1), 20, ground=tuple(px), source="marker")
    assert th.fix(tag, tuple(mapped)) == pytest.approx(xy, abs=0.02)


def test_only_raised_tags_are_corrected():
    from vision.detector import Detection

    th = TagHeights(2.5, (1.0, 0.5), {20: 0.8})
    box = Detection("vehicle", "car", 0.9, (0, 0, 1, 1), 20)                       # YOLO box bottom: on the floor
    other = Detection("vehicle", "car", 1.0, (0, 0, 1, 1), 21, source="marker")    # tag with no height given
    blob = Detection("vehicle", "chair", 0.5, (0, 0, 1, 1), 20, source="blob")     # blob stands in for tag 20
    assert th.fix(box, (2.0, 0.5)) == (2.0, 0.5)
    assert th.fix(other, (2.0, 0.5)) == (2.0, 0.5)
    assert th.fix(blob, (2.0, 0.5)) == pytest.approx((1.68, 0.5))
    assert th.fix(blob, None) is None


def test_layout_reads_camera_and_tag_heights(tmp_path):
    p = tmp_path / "layout.json"
    p.write_text('{"marker_size_m": 0.12, "markers": {"0": [0, 0], "1": [2.25, 0]}, '
                 '"camera_height_m": 2.5, "nadir_m": [1.125, 0.5], "tag_heights_m": {"20": 0.8, "10": 1.59}}')
    th = FloorLayout.load(p).tag_heights()
    assert th.camera_height_m == 2.5 and th.nadir_m == (1.125, 0.5) and th.heights_m == {20: 0.8, 10: 1.59}
    p.write_text('{"marker_size_m": 0.12, "markers": {"0": [0, 0]}}')
    assert FloorLayout.load(p).tag_heights() is None                # old layouts: no correction
    with pytest.raises(ValueError):
        TagHeights(2.5, (0, 0), {20: 3.0})                          # a tag above the camera is a typo
