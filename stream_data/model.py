"""The computer vision model the phone frames are fed into.

A model is any callable:

    model(img, t_s) -> dict
        img   H x W x 3 uint8 numpy array, BGR (OpenCV order), the newest frame
        t_s   when the frame arrived, pylsl.local_clock() seconds
        dict  anything JSON-serialisable; it is sent back to the phone and printed

Plug in your own with `python server.py --model my_module:MyModel`: a class (built
with no arguments) or a ready-made callable, importable from this folder. It runs in a worker
thread, one frame at a time, so it may block.
"""
from __future__ import annotations

import importlib

import numpy as np


class NullModel:
    """No model yet: proves frames arrive and reports their size and brightness."""
    name = "null"

    def __call__(self, img: np.ndarray, t_s: float) -> dict:
        return {"shape": list(img.shape), "mean_brightness": round(float(img.mean()), 1)}


class YoloModel:
    """Ultralytics YOLO object detection (pip install ultralytics). Weights download on first use."""

    def __init__(self, weights: str = "yolo11n.pt", conf: float = 0.4):
        from ultralytics import YOLO
        self.name = f"yolo ({weights})"
        self.net, self.conf = YOLO(weights), conf

    def __call__(self, img: np.ndarray, t_s: float) -> dict:
        r = self.net(img, conf=self.conf, verbose=False)[0]
        boxes = []
        for xyxy, c, k in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist()):
            boxes.append({"label": r.names[int(k)], "conf": round(c, 3), "xyxy": [round(v, 1) for v in xyxy]})
        return {"detections": boxes}


def load_model(spec: str):
    """'null' | 'yolo' | 'yolo:<weights.pt>' | '<module>:<attr>'."""
    kind, _, arg = spec.partition(":")
    if kind == "null":
        return NullModel()
    if kind == "yolo":
        return YoloModel(arg or "yolo11n.pt")
    if not arg:
        raise SystemExit(f"unknown model {spec!r}: use null, yolo, yolo:<weights.pt> or <module>:<callable>")
    obj = getattr(importlib.import_module(kind), arg)
    return obj() if isinstance(obj, type) else obj
