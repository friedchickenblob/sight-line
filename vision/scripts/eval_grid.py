"""Experiment 1 (guide pages 4–5): what is the machine called, and detection-rate grid.

Usage (run from vision/):
  python scripts/eval_grid.py names A                 # what classes appear on clip A
  python scripts/eval_grid.py grid --car truck        # model × imgsz grid -> results/grid.md

--car is what YOLO calls the machine: a COCO class name (car, truck, ...) or number. Default: car.
Clips that don't exist yet show as "n/a" instead of stopping the run.
"""
import argparse, pathlib
from collections import Counter
import cv2
import numpy as np
from ultralytics import YOLO

PERSON = 0
CAR = 2  # set from --car


def clip(name):
    p = pathlib.Path("clips") / f"{name}.mp4"
    return str(p) if p.exists() else None


def video_fps(path):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return fps or 30.0


def names_seen(model, clip):
    """How many frames each class appears in (at a low confidence, to see near-misses too)."""
    seen, n = Counter(), 0
    for r in model.predict(clip, stream=True, conf=0.15, verbose=False):
        n += 1
        seen.update({model.names[int(c)] for c in r.boxes.cls})
    return n, seen.most_common(10)


def detection_rate(model, clip, classes, conf=0.25, imgsz=640):
    n, hits, confs = 0, Counter(), {k: [] for k in classes}
    for r in model.predict(clip, stream=True, conf=conf, imgsz=imgsz, classes=classes, verbose=False):
        n += 1
        best = {}
        for k, c in zip(r.boxes.cls.int().tolist(), r.boxes.conf.tolist()):
            best[k] = max(best.get(k, 0), c)
        for k, c in best.items():
            hits[k] += 1; confs[k].append(c)
    return {k: (hits[k] / max(n, 1), float(np.median(confs[k] or [0]))) for k in classes}


def both_rate(model, clip, conf=0.25, imgsz=640):
    """Share of frames where machine AND person are both found (clip C)."""
    n = both = 0
    for r in model.predict(clip, stream=True, conf=conf, imgsz=imgsz, classes=[CAR, PERSON], verbose=False):
        n += 1
        both += {CAR, PERSON} <= set(r.boxes.cls.int().tolist())
    return both / max(n, 1)


def false_boxes_per_min(model, clip, conf=0.25, imgsz=640):
    """Clip G: empty floor / distractors. Any machine box is false; extra persons are allowed."""
    n = boxes = 0
    for r in model.predict(clip, stream=True, conf=conf, imgsz=imgsz, classes=[CAR], verbose=False):
        n += 1; boxes += len(r.boxes)
    return boxes / max(n / video_fps(clip) / 60, 1e-9)


def grid():
    rows = [("yolo26n.pt", 640), ("yolo26n.pt", 960), ("yolo26s.pt", 640), ("yolo11n.pt", 640)]
    clips = {k: clip(k) for k in "ABCDG"}
    missing = [k for k, p in clips.items() if p is None]
    if missing:
        print(f"missing clips (shown as n/a): {', '.join(missing)}")
    if len(missing) == len(clips):
        raise SystemExit("no clips in clips/: record them first (RECORDING-PLAN.md)")

    def cell(k, f, fmt):
        return format(f(clips[k]), fmt) if clips[k] else "n/a"

    car = YOLO(rows[0][0]).names[CAR]
    lines = [f"Machine class: {CAR} ({car})", "",
             f"| model | imgsz | {car} A | {car} D | person B | both C | false/min G |",
             "|---|---|---|---|---|---|---|"]
    for w, sz in rows:
        m = YOLO(w)
        a = cell("A", lambda p: detection_rate(m, p, [CAR], imgsz=sz)[CAR][0], ".0%")
        d = cell("D", lambda p: detection_rate(m, p, [CAR], imgsz=sz)[CAR][0], ".0%")
        b = cell("B", lambda p: detection_rate(m, p, [PERSON], imgsz=sz)[PERSON][0], ".0%")
        c = cell("C", lambda p: both_rate(m, p, imgsz=sz), ".0%")
        g = cell("G", lambda p: false_boxes_per_min(m, p, imgsz=sz), ".1f")
        lines.append(f"| {w} | {sz} | {a} | {d} | {b} | {c} | {g} |")
        print(lines[-1])
    pathlib.Path("results").mkdir(exist_ok=True)
    pathlib.Path("results/grid.md").write_text("\n".join(lines) + "\n")
    print("wrote results/grid.md")


def class_id(names, value):
    if value.isdigit():
        return int(value)
    ids = {v: k for k, v in names.items()}
    if value not in ids:
        raise SystemExit(f"unknown class {value!r}; e.g. car, truck, bus")
    return ids[value]


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["names", "grid"])
    p.add_argument("clip", nargs="?", default="A", help="clip letter for 'names'")
    p.add_argument("--car", default="car", help="what YOLO calls the machine (name or COCO id)")
    a = p.parse_args()
    model = YOLO("yolo26n.pt")
    CAR = class_id(model.names, a.car)
    if a.cmd == "names":
        path = clip(a.clip)
        if not path:
            raise SystemExit(f"no clips/{a.clip}.mp4")
        n, top = names_seen(model, path)
        print(f"clip {a.clip}, {n} frames. Frames each class appears in:")
        for name, k in top:
            print(f"  {name:12s} {k:5d}  ({k / max(n, 1):.0%})")
    else:
        grid()
