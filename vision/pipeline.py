"""One frame in, one scene out: floor calibration + YOLO + hazard geometry, plus the overlay.

Shared by the webcam / video app (run_vision.py) and the phone-stream app (run_phone.py),
so both behave the same. No window or camera handling here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np

from vision.detector import Detection, Detector
from vision.floor import FloorLayout, FloorMap, calibrate, detect_markers
from vision.overlay import GREEN, ORANGE, RED, WHITE, YELLOW, draw_grid, draw_head_arrow, label, pt
from vision.scene import Scene, WorkerSelector, build_scene


@dataclass
class FrameResult:
    scene: Scene
    calib_status: str
    infer_ms: float
    frame: int = 0


class VisionPipeline:
    """Holds what persists between frames: the floor map, the tracker's worker and the head yaw.

    layout          floor_layout.json contents: recalibrate from the markers every frame (unless frozen)
    floor_map       a saved calibration: used as-is (frozen) until unfrozen with a layout
    hazard_markers  {marker id: label}: an ArUco tag stuck on top of a vehicle (e.g. {4: "toy car"}).
                    Found every frame regardless of YOLO; its centre is the vehicle's floor point.
    """

    def __init__(self, detector: Detector, layout: FloorLayout | None = None,
                 floor_map: FloorMap | None = None, head_yaw_deg: float = 0.0,
                 hazard_markers: dict[int, str] | None = None, dictionary: str | None = None):
        self.detector, self.layout, self.floor_map = detector, layout, floor_map
        self.frozen = floor_map is not None
        self.head_yaw_deg = head_yaw_deg
        self.selector = WorkerSelector()
        self.hazard_markers = dict(hazard_markers or {})
        self.dictionary = dictionary or (layout.dictionary if layout else "DICT_4X4_50")
        clash = set(self.hazard_markers) & set(layout.markers if layout else ())
        if clash:
            raise ValueError(f"marker ids {sorted(clash)} are floor markers; tag the vehicle with another id")
        self.frames = 0

    def process(self, frame: np.ndarray) -> FrameResult:
        self.frames += 1
        found = None
        if self.layout is not None or self.hazard_markers:
            found = detect_markers(frame, self.dictionary)      # one search serves the floor and the tags

        status = "no floor map (pixels only)"
        if self.layout is not None and not self.frozen:
            try:
                self.floor_map, missing = calibrate(frame, self.layout, found)
                status = f"live calib, missing {missing}" if missing else "live calib, all markers"
            except ValueError:
                status = "live calib: markers not seen" + (" (using last)" if self.floor_map else "")
        elif self.floor_map is not None:
            status = "calibration fixed"

        t0 = time.perf_counter()
        dets = self.detector(frame)
        infer_ms = (time.perf_counter() - t0) * 1000
        if self.hazard_markers:
            dets = add_marker_hazards(dets, found or {}, self.hazard_markers)
        scene = build_scene(dets, self.floor_map, self.head_yaw_deg, self.selector)
        return FrameResult(scene, status, infer_ms, self.frames)

    # ------------------------------------------------------------- keyboard
    def nudge_yaw(self, step_deg: float) -> None:
        self.head_yaw_deg = (self.head_yaw_deg + step_deg + 180) % 360 - 180

    def toggle_freeze(self) -> None:
        if self.floor_map is not None:
            self.frozen = not self.frozen

    # -------------------------------------------------------------- drawing
    def render(self, frame: np.ndarray, res: FrameResult, header: str = "") -> np.ndarray:
        view = frame.copy()
        if self.floor_map is not None:
            draw_grid(view, self.floor_map, self.layout)
        draw_scene(view, res.scene, self.floor_map, self.head_yaw_deg)
        if header:
            label(view, header, (10, 25), WHITE, 0.5)
        return view


def add_marker_hazards(dets: list[Detection], found: dict[int, np.ndarray],
                       hazard_markers: dict[int, str]) -> list[Detection]:
    """Turn tagged vehicles' markers into vehicle detections (track id = marker id).

    A YOLO vehicle box that contains a tag's centre is the same object: the tag replaces it,
    because the tag's centre is a better floor point than a box bottom seen at an angle."""
    tags = []
    for mid, lbl in hazard_markers.items():
        if mid in found:
            c = found[mid]
            (x1, y1), (x2, y2) = c.min(axis=0), c.max(axis=0)
            cx, cy = c.mean(axis=0)
            tags.append(Detection("vehicle", lbl, 1.0, (float(x1), float(y1), float(x2), float(y2)), mid,
                                  ground=(float(cx), float(cy)), source="marker"))

    def covered(d: Detection) -> bool:
        return d.kind == "vehicle" and any(
            d.box[0] <= t.ground[0] <= d.box[2] and d.box[1] <= t.ground[1] <= d.box[3] for t in tags)

    return [d for d in dets if not covered(d)] + tags


def draw_scene(view, scene: Scene, fm: FloorMap | None, yaw: float) -> None:
    w = scene.worker
    for p in scene.other_people:
        x1, y1, x2, y2 = p.det.box
        cv2.rectangle(view, pt((x1, y1)), pt((x2, y2)), GREEN, 1)
        label(view, f"person #{p.det.track_id} {p.det.conf:.2f}", (x1, y1 - 6), GREEN, 0.45)
    if w:
        x1, y1, x2, y2 = w.det.box
        cv2.rectangle(view, pt((x1, y1)), pt((x2, y2)), YELLOW, 2)
        cv2.circle(view, pt(w.det.ground_px), 6, YELLOW, -1)
        txt = f"WORKER #{w.det.track_id} {w.det.conf:.2f}"
        if w.floor_xy:
            txt += f"  ({w.floor_xy[0]:.2f}, {w.floor_xy[1]:.2f}) m"
            draw_head_arrow(view, fm, w.floor_xy, yaw)
        label(view, txt, (x1, y1 - 6), YELLOW)
    for r in scene.hazards:
        d = r.hazard.det
        x1, y1, x2, y2 = d.box
        cv2.rectangle(view, pt((x1, y1)), pt((x2, y2)), ORANGE, 2)
        cv2.circle(view, pt(d.ground_px), 6, ORANGE, -1)
        txt = f"{d.label} " + (f"tag {d.track_id}" if d.source == "marker" else f"#{d.track_id} {d.conf:.2f}")
        if r.distance_m is not None:
            txt += f"  {r.distance_m:.2f} m  brg {r.bearing_deg:+.0f}  rel {r.rel_bearing_deg:+.0f}"
            cv2.line(view, pt(w.det.ground_px), pt(d.ground_px), RED, 2, cv2.LINE_AA)
        label(view, txt, (x1, y1 - 6), ORANGE)


def summary(scene: Scene) -> str:
    w = scene.worker
    s = "worker: none" if not w else (
        f"worker #{w.det.track_id} " + (f"at ({w.floor_xy[0]:.2f}, {w.floor_xy[1]:.2f}) m" if w.floor_xy else "(no floor map)"))
    for r in scene.hazards:
        d = r.hazard.det
        s += f" | {d.label} #{d.track_id}"
        if r.distance_m is not None:
            s += f": {r.distance_m:.2f} m, bearing {r.bearing_deg:+.0f} deg"
            if r.rel_bearing_deg is not None:
                s += f", relative to head {r.rel_bearing_deg:+.0f} deg"
    return s


def _r(v, nd=3):
    return None if v is None else round(float(v), nd)


def scene_to_dict(res: FrameResult, head_yaw_deg: float | None) -> dict:
    """One sightline/vision/hazards message (docs/vision-hazard-plan.md), JSON-ready.

    Floor frame: metres from marker 0; bearing_deg is from the worker to the hazard (0 = +x, CCW +);
    rel_bearing_deg is that minus the head yaw (0 ahead, +90 left, -90 right). The caller adds t_s.
    "detections" is for the phone stream's /view page ({label, conf, xyxy} boxes)."""
    sc = res.scene

    def xy(p):
        return (None, None) if p.floor_xy is None else (_r(p.floor_xy[0]), _r(p.floor_xy[1]))

    worker = None
    if sc.worker:
        x, y = xy(sc.worker)
        worker = {"track_id": sc.worker.det.track_id, "x_m": x, "y_m": y, "conf": _r(sc.worker.det.conf)}
    hazards = []
    for r in sc.hazards:
        d = r.hazard.det
        x, y = xy(r.hazard)
        hazards.append({"track_id": d.track_id, "kind": d.kind, "label": d.label, "source": d.source,
                        "conf": _r(d.conf), "x_m": x, "y_m": y, "distance_m": _r(r.distance_m),
                        "bearing_deg": _r(r.bearing_deg, 1), "rel_bearing_deg": _r(r.rel_bearing_deg, 1)})

    def box(det, text):
        return {"label": text, "conf": _r(det.conf, 2), "xyxy": [_r(v, 1) for v in det.box]}

    boxes = [box(sc.worker.det, "WORKER")] if sc.worker else []
    boxes += [box(p.det, "person") for p in sc.other_people]
    for r, h in zip(sc.hazards, hazards):
        text = r.hazard.det.label
        if h["bearing_deg"] is not None:
            text += f" {h['distance_m']:.2f}m {h['bearing_deg']:+.0f}deg"
        boxes.append(box(r.hazard.det, text))

    return {"v": 1, "frame": res.frame, "calib": res.calib_status, "infer_s": _r(res.infer_ms / 1000, 4),
            "head_yaw_deg": _r(head_yaw_deg, 1), "worker": worker, "hazards": hazards, "detections": boxes}
