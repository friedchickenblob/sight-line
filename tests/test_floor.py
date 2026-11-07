"""Floor mapping with a simulated ANGLED camera: render the markers on a virtual floor, photograph
them with a pinhole camera 1.8 m up looking down at the floor, then check that detection +
calibration recover true floor positions."""

import cv2
import numpy as np
import pytest

from vision.floor import FloorLayout, FloorMap, calibrate, ground_point

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


def render(rotate_marker: int | None = None):
    S, ox, oy = 400, 0.5, 2.5               # canvas: 400 px/m covering x -0.5..3.5, y -0.5..2.5
    canvas = np.full((int(3.0 * S), int(4.0 * S)), 255, np.uint8)
    dic = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    side = int(LAYOUT.marker_size_m * S)
    for mid, (cx, cy) in LAYOUT.markers.items():
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
