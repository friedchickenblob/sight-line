"""Temporary pixel -> floor-metre map, fitted from floor markers 0-3 seen in the clip itself.

Stand-in for Hanh's geometry.py / floor_calib.json (same idea as Hanh's guide, page 6), so tracking
works in real metres before those land. Swap it out as soon as they do.

Needs clips/floor.json with the MEASURED centre of each floor marker in metres (floor frame: origin at
marker 0, +x toward marker 1, +y toward marker 3). See clips/floor.example.json. Optional fields
camera_height_m and nadir_m (point under the camera) are used for the worker parallax fix.
"""
import json, pathlib
import cv2
import numpy as np

FLOOR_IDS = (0, 1, 2, 3)
CONFIG = pathlib.Path("clips/floor.json")
PLACEHOLDER_H = np.diag([1 / 500, 1 / 500, 1.0])  # pixels / 500 ≈ metres: only so the pipeline runs

_det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),
                               cv2.aruco.DetectorParameters())


def load_config(path=CONFIG):
    path = pathlib.Path(path)
    if not path.exists():
        return None
    cfg = json.loads(path.read_text())
    cfg["markers_m"] = {int(k): tuple(v) for k, v in cfg["markers_m"].items()}
    return cfg


def to_floor(H, points):
    """Pixels [(u, v), ...] -> floor metres, an (N, 2) array. Same signature as Hanh's."""
    pts = np.float32(points).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, np.asarray(H, dtype=np.float64)).reshape(-1, 2)


def detect(frame):
    """All ArUco markers in a frame: {id: (4, 2) array of corner pixels}."""
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = _det.detectMarkers(gray)
    if ids is None:
        return {}
    return {int(i): c.reshape(4, 2) for c, i in zip(corners, ids.flatten())}


def side_px(corners):
    """Shortest side of a marker in pixels: the number that decides whether it is still readable."""
    return float(min(np.linalg.norm(corners[k] - corners[(k + 1) % 4]) for k in range(4)))


def in_rig(xy, markers_m, margin_m=0.3):
    """True if a floor point is inside the box spanned by the floor markers, plus a margin.
    Used to ignore people and objects outside the taped area (the string puller, the room)."""
    pts = np.array([markers_m[i] for i in FLOOR_IDS if i in markers_m], dtype=float)
    lo, hi = pts.min(axis=0) - margin_m, pts.max(axis=0) + margin_m
    return bool(np.all(np.asarray(xy) >= lo) and np.all(np.asarray(xy) <= hi))


def fit_from_frame(frame, markers_m):
    """Fit H from one frame. Returns (H, worst_error_m), or (None, None) if a floor marker is missing."""
    found = detect(frame)
    seen = {i: c.mean(axis=0) for i, c in found.items() if i in markers_m}
    if not all(i in seen for i in FLOOR_IDS):
        return None, None
    keys = sorted(seen)
    src = np.float32([seen[k] for k in keys])
    dst = np.float32([markers_m[k] for k in keys])
    H, _ = cv2.findHomography(src, dst)
    if H is None:
        return None, None
    err = float(np.linalg.norm(to_floor(H, src) - dst, axis=1).max())  # 0 with exactly 4 markers
    return H, err


def parallax_fix(xy, h_m, cfg):
    """Move a point seen at height h_m back to the floor point under it (Hanh's guide, page 7):
    P_true = N + (P_from_H - N) * (C - h) / C. Returns xy unchanged if C or N isn't in the config."""
    if not cfg or "camera_height_m" not in cfg or "nadir_m" not in cfg:
        return xy
    C, N = cfg["camera_height_m"], np.array(cfg["nadir_m"], dtype=float)
    return tuple(float(v) for v in N + (np.asarray(xy, dtype=float) - N) * (C - h_m) / C)
