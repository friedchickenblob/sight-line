"""Experiment 1 (guide pages 4–5): what is the machine called, and detection-rate grid.

Usage:
  python scripts/eval_grid.py names A            # what classes appear on clip A
  python scripts/eval_grid.py grid               # fills the model × imgsz grid -> results/grid.md
"""
import sys
from collections import Counter
import numpy as np
from ultralytics import YOLO

CAR, PERSON = 2, 0   # set CAR to whatever the machine is consistently called (e.g. 7 truck)


def names_seen(model, clip):
    seen = Counter()
    for r in model.predict(clip, stream=True, conf=0.15, verbose=False):
        seen.update(model.names[int(c)] for c in r.boxes.cls)
    return seen.most_common(10)


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


def false_boxes_per_min(model, clip, conf=0.25, imgsz=640, fps=30):
    """Clip G: empty floor / distractors. Any machine box is false; extra persons are allowed."""
    n = boxes = 0
    for r in model.predict(clip, stream=True, conf=conf, imgsz=imgsz, classes=[CAR], verbose=False):
        n += 1; boxes += len(r.boxes)
    return boxes / max(n / fps / 60, 1e-9)


def grid():
    rows = [("yolo26n.pt", 640), ("yolo26n.pt", 960), ("yolo26s.pt", 640), ("yolo11n.pt", 640)]
    lines = ["| model | imgsz | car A | car D | person B | both C | false/min G |", "|---|---|---|---|---|---|---|"]
    for w, sz in rows:
        m = YOLO(w)
        a = detection_rate(m, "clips/A.mp4", [CAR], imgsz=sz)[CAR][0]
        d = detection_rate(m, "clips/D.mp4", [CAR], imgsz=sz)[CAR][0]
        b = detection_rate(m, "clips/B.mp4", [PERSON], imgsz=sz)[PERSON][0]
        c = both_rate(m, "clips/C.mp4", imgsz=sz)
        g = false_boxes_per_min(m, "clips/G.mp4", imgsz=sz)
        lines.append(f"| {w} | {sz} | {a:.0%} | {d:.0%} | {b:.0%} | {c:.0%} | {g:.1f} |")
        print(lines[-1])
    open("results/grid.md", "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    if sys.argv[1] == "names":
        print(names_seen(YOLO("yolo26n.pt"), f"clips/{sys.argv[2]}.mp4"))
    else:
        grid()
