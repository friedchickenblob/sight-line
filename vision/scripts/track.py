"""Step 7 (guide page 9): ByteTrack -> lock-on machine -> floor metres -> velocity -> CSV.

Usage: python scripts/track.py clips/D.mp4 results/tracks_D.csv
       python scripts/track.py 0                     # live webcam, no CSV

`to_floor` is a placeholder until Hanh's geometry.py lands; replace the import below.
"""
import csv, sys, time
from collections import deque
import cv2
import numpy as np
from ultralytics import YOLO

try:
    from geometry import to_floor, H  # Hanh's shared module
except ImportError:
    H = None
    def to_floor(H, points):  # placeholder: pixels / 500 ≈ metres, just so the pipeline runs
        return [(x / 500, y / 500) for x, y in points]

try:
    from pylsl import local_clock
except ImportError:
    local_clock = time.perf_counter

WEIGHTS = "yolo26n.pt"
MACHINE_CLS, WORKER_CLS = {2, 7}, {0}  # COCO; after fine-tune use {1}, {0}
RELOCK_AFTER_S = 0.5


class Velocity:
    """Straight-line fit over the last ~0.5 s of floor positions."""
    def __init__(self, n=15):
        self.hist = deque(maxlen=n)

    def update(self, t, x, y):
        self.hist.append((t, x, y))
        if len(self.hist) < 5:
            return None
        a = np.array(self.hist)
        return np.polyfit(a[:, 0], a[:, 1], 1)[0], np.polyfit(a[:, 0], a[:, 2], 1)[0]


class MachineLock:
    """There is exactly one machine: stick to one track, re-lock by prediction when lost."""
    def __init__(self):
        self.id, self.last_t, self.last_xy, self.v = None, None, None, (0.0, 0.0)

    def pick(self, t, candidates):  # candidates: [(tid, x_m, y_m)]
        for c in candidates:
            if c[0] == self.id:
                return c
        if self.id is not None and t - self.last_t < RELOCK_AFTER_S:
            return None  # briefly lost: let the tracker coast
        if not candidates:
            return None
        if self.last_xy is None:
            best = candidates[0]
        else:
            dt = t - self.last_t
            px, py = self.last_xy[0] + self.v[0] * dt, self.last_xy[1] + self.v[1] * dt
            best = min(candidates, key=lambda c: (c[1] - px) ** 2 + (c[2] - py) ** 2)
        self.id = best[0]
        return best

    def seen(self, t, x, y, v):
        self.last_t, self.last_xy = t, (x, y)
        if v is not None:
            self.v = v


def frames(source):
    """Yield (t, frame), with t taken when the frame was captured.

    Live camera (int source): pylsl.local_clock() right after cap.read().
    Video file: the frame's own time in the video (frame index / fps), so velocities are right
    however fast or slow the model runs. Convert phone clips to a constant 30 fps first.
    """
    live = isinstance(source, int)
    if live:
        from record_clips import open_cam
        cap = open_cam(source)
    else:
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            raise SystemExit(f"can't open {source}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    i = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            yield (local_clock() if live else i / fps), frame
            i += 1
    finally:
        cap.release()


def run(source, out_csv=None):
    model = YOLO(WEIGHTS)
    lock, vel = MachineLock(), {}
    writer = csv.writer(open(out_csv, "w", newline="")) if out_csv else None
    if writer:
        writer.writerow(["t", "id", "cls", "x_m", "y_m", "vx", "vy"])
    for t, frame in frames(source):
        # conf is low on purpose: ByteTrack uses weak boxes in its second matching pass
        r = model.track(frame, persist=True, tracker="bytetrack.yaml", conf=0.1, imgsz=640, verbose=False)[0]
        if r.boxes.id is None:
            continue
        machines, workers = [], []
        for (x1, y1, x2, y2), tid, k in zip(r.boxes.xyxy.tolist(), r.boxes.id.int().tolist(),
                                            r.boxes.cls.int().tolist()):
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            # TODO worker: apply Hanh's parallax correction (h ≈ 1.3 m) or use hardhat marker
            (xm, ym), = to_floor(H, [(cx, cy)])
            (machines if k in MACHINE_CLS else workers if k in WORKER_CLS else []).append((tid, xm, ym))

        rows = []
        m = lock.pick(t, machines)
        if m:
            v = vel.setdefault("machine", Velocity()).update(t, m[1], m[2])
            lock.seen(t, m[1], m[2], v)
            rows.append((m[0], "machine", m[1], m[2], v))
        for tid, xm, ym in workers:
            rows.append((tid, "worker", xm, ym, vel.setdefault(tid, Velocity()).update(t, xm, ym)))

        for tid, cls, xm, ym, v in rows:
            vx, vy = v if v else (float("nan"), float("nan"))
            if writer:
                writer.writerow([f"{t:.4f}", tid, cls, f"{xm:.3f}", f"{ym:.3f}", f"{vx:.3f}", f"{vy:.3f}"])
            else:
                print(f"{t:9.3f} {cls:7s} id={tid:<3} ({xm:5.2f},{ym:5.2f}) m  v=({vx:+.2f},{vy:+.2f}) m/s")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "0"
    run(int(src) if src.isdigit() else src, sys.argv[2] if len(sys.argv) > 2 else None)
