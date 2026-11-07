"""Image pixels -> floor metres, using ArUco markers on the floor (works for an angled camera).

The floor layout file says where each marker's centre is (tape-measured, metres) and how big
the printed black square is. Markers must be laid with their TOP edge facing +y (toward
marker 3), so each corner's floor position is known; a marker laid rotated shows up as a
large per-marker error in the calibration report.

floor_layout.json example:
    {"dictionary": "DICT_4X4_50", "marker_size_m": 0.15,
     "markers": {"0": [0.0, 0.0], "1": [3.0, 0.0], "2": [3.0, 2.0], "3": [0.0, 2.0]}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np


# --------------------------------------------------------------------------- layout
@dataclass
class FloorLayout:
    dictionary: str
    marker_size_m: float
    markers: dict[int, tuple[float, float]]   # id -> centre (x, y) in metres

    @classmethod
    def load(cls, path: str | Path) -> "FloorLayout":
        d = json.loads(Path(path).read_text())
        return cls(d.get("dictionary", "DICT_4X4_50"), float(d["marker_size_m"]),
                   {int(k): (float(v[0]), float(v[1])) for k, v in d["markers"].items()})

    def corners_m(self, marker_id: int) -> np.ndarray:
        """Floor positions of a marker's corners in ArUco order: TL, TR, BR, BL (top faces +y)."""
        cx, cy = self.markers[marker_id]
        h = self.marker_size_m / 2
        return np.array([[cx - h, cy + h], [cx + h, cy + h], [cx + h, cy - h], [cx - h, cy - h]])


def aruco_dictionary(name: str):
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))


def detect_markers(image: np.ndarray, dictionary_name: str = "DICT_4X4_50") -> dict[int, np.ndarray]:
    """Return {marker id: 4x2 corner pixels (TL, TR, BR, BL)}."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    detector = cv2.aruco.ArucoDetector(aruco_dictionary(dictionary_name), cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is None:
        return {}
    return {int(i): c.reshape(4, 2).astype(float) for i, c in zip(ids.flatten(), corners)}


# --------------------------------------------------------------------------- mapping
@dataclass
class FloorMap:
    H: np.ndarray                                   # 3x3, pixel -> floor metres
    errors_m: dict[int, float] = field(default_factory=dict)  # mean corner error per marker

    @classmethod
    def fit(cls, pixels: np.ndarray, floor_m: np.ndarray) -> "FloorMap":
        pixels, floor_m = np.asarray(pixels, float), np.asarray(floor_m, float)
        if len(pixels) < 4:
            raise ValueError("need at least 4 point pairs to fit a homography")
        H, _ = cv2.findHomography(pixels, floor_m, 0)   # least squares over all points
        if H is None:
            raise ValueError("homography fit failed (points collinear?)")
        return cls(H)

    def to_floor(self, pixels) -> np.ndarray:
        """Pixel(s) (N,2) -> floor metres (N,2). Only valid for points ON the floor."""
        p = np.asarray(pixels, float).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(p, self.H).reshape(-1, 2)

    def to_pixel(self, floor_m) -> np.ndarray:
        """Floor metres (N,2) -> pixels (N,2), e.g. to draw a floor grid on the image."""
        p = np.asarray(floor_m, float).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(p, np.linalg.inv(self.H)).reshape(-1, 2)

    def save(self, path: str | Path, **extra) -> None:
        Path(path).write_text(json.dumps({"H": self.H.tolist(), "errors_m": self.errors_m, **extra}, indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "FloorMap":
        d = json.loads(Path(path).read_text())
        return cls(np.array(d["H"], float), {int(k): v for k, v in d.get("errors_m", {}).items()})


def calibrate(image: np.ndarray | None, layout: FloorLayout,
              found: dict[int, np.ndarray] | None = None) -> tuple[FloorMap, list[int]]:
    """Detect the layout's markers in an image and fit the floor map.

    Pass `found` (from detect_markers) to reuse a detection instead of searching the image again.
    Returns the map (with per-marker corner error in metres) and the ids that were missing.
    """
    if found is None:
        found = detect_markers(image, layout.dictionary)
    used = [i for i in layout.markers if i in found]
    missing = [i for i in layout.markers if i not in found]
    if len(used) < 2:   # 2 markers = 8 corners: enough points, but use all 4 for a reliable fit
        raise ValueError(f"only saw markers {used}; need at least 2 (ideally all of {list(layout.markers)})")
    px = np.vstack([found[i] for i in used])
    fl = np.vstack([layout.corners_m(i) for i in used])
    fm = FloorMap.fit(px, fl)
    for i in used:
        err = np.linalg.norm(fm.to_floor(found[i]) - layout.corners_m(i), axis=1).mean()
        fm.errors_m[i] = float(err)
    return fm, missing


def ground_point(box_xyxy) -> tuple[float, float]:
    """Pixel where a detected object touches the floor: bottom-centre of its bounding box."""
    x1, _, x2, y2 = box_xyxy
    return ((x1 + x2) / 2.0, float(y2))
