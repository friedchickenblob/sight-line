"""Person + vehicle detection with Ultralytics YOLO26, plus ByteTrack track IDs.

Stock COCO weights: "person" -> kind "person"; road vehicles -> kind "vehicle".
The RC machine may or may not be recognised as a vehicle by stock weights, especially
from above: that's the day-one feasibility test in docs/vision-hazard-plan.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from vision.floor import ground_point

PERSON_LABELS = frozenset({"person"})
VEHICLE_LABELS = frozenset({"bicycle", "car", "motorcycle", "bus", "truck", "train"})


@dataclass
class Detection:
    kind: str                                  # "person" | "vehicle"
    label: str                                 # COCO class name, e.g. "car"
    conf: float
    box: tuple[float, float, float, float]     # x1, y1, x2, y2 pixels
    track_id: int | None = None

    @property
    def ground_px(self) -> tuple[float, float]:
        """Where it touches the floor in the image (bottom-centre of the box)."""
        return ground_point(self.box)


def kind_of(label: str, vehicle_labels=VEHICLE_LABELS) -> str | None:
    if label in PERSON_LABELS:
        return "person"
    if label in vehicle_labels:
        return "vehicle"
    return None


class Detector:
    def __init__(self, model: str = "yolo26n.pt", conf: float = 0.35, imgsz: int = 640,
                 track: bool = True, vehicle_labels=VEHICLE_LABELS, device: str | None = None):
        from ultralytics import YOLO   # imported here so the rest of vision/ works without it

        self.model = YOLO(model)
        self.conf, self.imgsz, self.track, self.device = conf, imgsz, track, device
        self.vehicle_labels = frozenset(vehicle_labels)
        names = self.model.names
        self.class_ids = [i for i, n in names.items() if kind_of(n, self.vehicle_labels)]
        unknown = set(self.vehicle_labels) - set(names.values())
        if unknown:
            raise ValueError(f"model has no classes {sorted(unknown)}; it knows {sorted(names.values())}")

    def __call__(self, frame: np.ndarray) -> list[Detection]:
        kw = dict(conf=self.conf, imgsz=self.imgsz, classes=self.class_ids, verbose=False, device=self.device)
        if self.track:
            r = self.model.track(frame, persist=True, tracker="bytetrack.yaml", **kw)[0]
        else:
            r = self.model.predict(frame, **kw)[0]
        boxes = r.boxes
        if boxes is None or len(boxes) == 0:
            return []
        xyxy = boxes.xyxy.cpu().numpy()
        cls = boxes.cls.cpu().numpy().astype(int)
        conf = boxes.conf.cpu().numpy()
        ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else [None] * len(cls)
        out = []
        for b, c, p, i in zip(xyxy, cls, conf, ids):
            label = r.names[int(c)]
            kind = kind_of(label, self.vehicle_labels)
            if kind:
                out.append(Detection(kind, label, float(p), tuple(float(v) for v in b),
                                     None if i is None else int(i)))
        return out
