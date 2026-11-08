"""Step 7 (guide page 9): ByteTrack -> lock-on machine -> floor metres -> velocity -> CSV.

Usage: python scripts/track.py clips/D.mp4 results/tracks_D.csv
       python scripts/track.py 0                     # live webcam, no CSV
       python scripts/track.py clips/D.mp4 out.csv --machine yolo   # old way: machine = YOLO car/truck box

Machine (default --machine marker): the hazard is an office chair carrying ArUco marker 20 on top. Its
position comes from the marker, not from YOLO, because the room is full of other chairs. Set
machine_marker_height_m in clips/floor.json (marker height above the floor) for the parallax fix.
Workers: YOLO person boxes, kept only if they stand inside the taped area (floor markers 0-3 + 0.3 m),
so the string puller and people in the background are ignored.

Floor map, best first: Hanh's geometry.py (when it lands) -> fitted from floor markers 0-3 in the
clip, using the measured positions in clips/floor.json (floor_map.py) -> pixels / 500 placeholder.
The fitted map is refitted every REFIT_EVERY_S from the last place each floor marker was seen, so a
camera that sags or gets bumped mid-clip is followed, and one marker hidden by a person doesn't stop it.
For a video file, each marker's first sighting anywhere in the clip seeds the map, so a marker that is
covered at the start doesn't force the placeholder.
"""
import csv, sys, time
from collections import deque
import cv2
import numpy as np
from ultralytics import YOLO
import floor_map

try:
    from geometry import to_floor, H  # Hanh's shared module
except ImportError:
    H = None
    to_floor = floor_map.to_floor
FIT_WITHIN_FRAMES = 90  # live camera: look this long for all four floor markers before using the placeholder
REFIT_EVERY_S = 1.0     # refit the floor map this often, so a sagging or bumped camera doesn't skew positions
MOVED_PX = 10           # report when a floor marker has moved this far since the last report

try:
    from pylsl import local_clock
except ImportError:
    local_clock = time.perf_counter

WEIGHTS = "yolo26n.pt"
MACHINE_CLS, WORKER_CLS = {2, 7}, {0}  # COCO; after fine-tune use {1}, {0}
MACHINE_MARKER_ID = 20
RELOCK_AFTER_S = 0.5
WORKER_BOX_H_M = 1.3  # height of a person's box centre seen from above (guide page 9)
# Blob fallback: marker 20 is only ~20 px wide at 1280x720, so compression often makes it unreadable.
# The chair is the only big black shape on the floor, so between marker sightings we follow that blob.
BLOB_V_MAX = 45            # HSV value below this counts as "black chair"
BLOB_AREA_PX = (15000, 90000)
BLOB_MAX_JUMP_PX = 200     # blob centre must be this close to where the chair was last seen
BLOB_MAX_GAP_S = 30.0      # stop trusting the blob this long after the last marker sighting
RELOCK_BLOB_S = 1.0        # chair not found near its last spot for this long: take the biggest blob anywhere
# One worker (demo assumption): report a single worker track with a fixed id, from the hardhat marker
# when it's readable, else the YOLO person box nearest the last worker position.
WORKER_MARKER_ID = 10
WORKER_ID = 10
WORKER_MAX_JUMP_M = 0.8    # per second since the last fix: a person box farther than this is someone else


def chair_blob(frame, near_px=None):
    """Centre (u, v) of the big dark blob nearest near_px, or None.
    near_px=None (chair lost, e.g. carried back to the start line): the biggest chair-sized blob anywhere."""
    v = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[..., 2]
    mask = (v < BLOB_V_MAX).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, _, st, cen = cv2.connectedComponentsWithStats(mask)
    best = None
    for i in range(1, n):
        if not BLOB_AREA_PX[0] <= st[i, cv2.CC_STAT_AREA] <= BLOB_AREA_PX[1]:
            continue
        if near_px is None:
            d = -st[i, cv2.CC_STAT_AREA]  # biggest wins
        else:
            d = np.linalg.norm(cen[i] - near_px)
            if d > BLOB_MAX_JUMP_PX:
                continue
        if best is None or d < best[0]:
            best = (d, cen[i])
    return None if best is None else best[1]


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


def first_sightings(path, step=5):
    """Video file: the first pixel centre of each floor marker anywhere in the clip, {id: (u, v)}.
    Looks at every `step`-th frame and stops once all four are found."""
    cap, found, i = cv2.VideoCapture(path), {}, 0
    try:
        while len(found) < len(floor_map.FLOOR_IDS) and cap.grab():
            if i % step == 0:
                ok, frame = cap.retrieve()
                if ok:
                    for k, c in floor_map.detect(frame).items():
                        if k in floor_map.FLOOR_IDS and k not in found:
                            found[k] = c.mean(axis=0)
            i += 1
    finally:
        cap.release()
    return found


def run(source, out_csv=None, machine_by="marker"):
    model = YOLO(WEIGHTS)
    lock, vel = MachineLock(), {}
    writer = csv.writer(open(out_csv, "w", newline="")) if out_csv else None
    if writer:
        writer.writerow(["t", "id", "cls", "x_m", "y_m", "vx", "vy"])
    H_ = H
    cfg = floor_map.load_config()
    if H_ is None and cfg is None:
        print(f"no {floor_map.CONFIG}: using the pixels/500 placeholder (copy clips/floor.example.json)")
        H_ = floor_map.PLACEHOLDER_H
    fitting = H_ is None  # fit (and keep refitting) the floor map from the markers in the clip
    last_px = {}          # floor marker id -> last pixel centre seen
    if fitting and not isinstance(source, int):
        last_px = first_sightings(source)
        missing = [k for k in floor_map.FLOOR_IDS if k not in last_px]
        if missing:
            print(f"floor markers {missing} never seen in the clip: using the placeholder")
            H_, fitting = floor_map.PLACEHOLDER_H, False
    reported_px, last_fit_t, n_fits = dict(last_px), None, 0
    chair_px, marker_t, offset = None, -1e9, None  # last machine pixel position, last marker sighting
    chair_t, worker_xy, worker_t = -1e9, None, -1e9
    n_frames = n_marker = n_blob = n_hat = n_box = 0
    for i, (t, frame) in enumerate(frames(source)):
        found = floor_map.detect(frame)
        if fitting:
            last_px.update({k: c.mean(axis=0) for k, c in found.items() if k in floor_map.FLOOR_IDS})
            if last_fit_t is None or t - last_fit_t >= REFIT_EVERY_S:
                H_new, err = floor_map.fit_from_centres(last_px, cfg["markers_m"])
                if H_new is not None:
                    if H_ is None:
                        print(f"floor map fitted from markers 0-3 at frame {i} (fit error {err * 100:.1f} cm)")
                    H_, last_fit_t, n_fits = H_new, t, n_fits + 1
                    reported_px = reported_px or dict(last_px)  # live camera: first fit is the reference
                    moved = max(np.linalg.norm(last_px[k] - reported_px[k]) for k in floor_map.FLOOR_IDS)
                    if moved >= MOVED_PX:
                        print(f"t={t:.1f} s: floor markers moved {moved:.0f} px (camera moved), map refitted")
                        reported_px = dict(last_px)
            if H_ is None:
                if i + 1 >= FIT_WITHIN_FRAMES:
                    print(f"floor markers 0-3 not all seen in {FIT_WITHIN_FRAMES} frames: using the placeholder")
                    H_, fitting = floor_map.PLACEHOLDER_H, False
                else:
                    continue
        machines, workers = [], []
        if machine_by == "marker":
            c = found.get(MACHINE_MARKER_ID)
            px = None
            if c is not None:
                px = c.mean(axis=0)
                blob = chair_blob(frame, px)
                if blob is not None:
                    offset = px - blob  # marker sits off the blob centre
                marker_t, n_marker = t, n_marker + 1
            elif chair_px is not None and t - marker_t <= BLOB_MAX_GAP_S:
                lost = t - chair_t > RELOCK_BLOB_S  # chair moved too far to follow: look anywhere
                blob = chair_blob(frame, None if lost else chair_px)
                if blob is not None:
                    px = blob + (offset if offset is not None else 0)
                    n_blob += 1
            if px is not None:
                chair_px, chair_t = px, t
                (xm, ym), = to_floor(H_, [px])
                h = cfg.get("machine_marker_height_m", 0.0) if cfg else 0.0
                xm, ym = floor_map.parallax_fix((xm, ym), h, cfg)
                machines.append((MACHINE_MARKER_ID, xm, ym))
            n_frames += 1
        classes = sorted(WORKER_CLS) if machine_by == "marker" else sorted(WORKER_CLS | MACHINE_CLS)
        # conf is low on purpose: ByteTrack uses weak boxes in its second matching pass
        r = model.track(frame, persist=True, tracker="bytetrack.yaml", conf=0.1, imgsz=640, verbose=False,
                        classes=classes)[0]
        if r.boxes.id is not None:
            for (x1, y1, x2, y2), tid, k in zip(r.boxes.xyxy.tolist(), r.boxes.id.int().tolist(),
                                                r.boxes.cls.int().tolist()):
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                (xm, ym), = to_floor(H_, [(cx, cy)])
                if k in WORKER_CLS:  # box centre is ~chest height: undo parallax (TODO: prefer the hardhat marker)
                    xm, ym = floor_map.parallax_fix((xm, ym), WORKER_BOX_H_M, cfg)
                    if cfg and not floor_map.in_rig((xm, ym), cfg["markers_m"]):
                        continue  # outside the taped area: string puller, people in the background
                    workers.append((tid, xm, ym))
                elif k in MACHINE_CLS:
                    machines.append((tid, xm, ym))

        rows = []
        m = lock.pick(t, machines)
        if m:
            v = vel.setdefault("machine", Velocity()).update(t, m[1], m[2])
            lock.seen(t, m[1], m[2], v)
            rows.append((m[0], "machine", m[1], m[2], v))
        if machine_by == "marker":  # single worker: hardhat marker first, else nearest person box
            w = None
            c = found.get(WORKER_MARKER_ID)
            if c is not None:
                (xm, ym), = to_floor(H_, [c.mean(axis=0)])
                h = cfg.get("hardhat_marker_height_m", 1.6) if cfg else 1.6
                w = floor_map.parallax_fix((xm, ym), h, cfg)
                n_hat += 1
            elif workers:
                if worker_xy is None or t - worker_t > 2.0:
                    near = workers  # no recent fix: take any person in the rig
                else:
                    reach = WORKER_MAX_JUMP_M * max(t - worker_t, 0.25)
                    near = [p for p in workers if np.hypot(p[1] - worker_xy[0], p[2] - worker_xy[1]) <= reach]
                if near:
                    ref = worker_xy or near[0][1:]
                    best = min(near, key=lambda p: np.hypot(p[1] - ref[0], p[2] - ref[1]))
                    w = best[1:]
                    n_box += 1
            if w is not None:
                worker_xy, worker_t = w, t
                rows.append((WORKER_ID, "worker", w[0], w[1],
                             vel.setdefault("worker", Velocity()).update(t, w[0], w[1])))
        else:
            for tid, xm, ym in workers:
                rows.append((tid, "worker", xm, ym, vel.setdefault(tid, Velocity()).update(t, xm, ym)))

        for tid, cls, xm, ym, v in rows:
            vx, vy = v if v else (float("nan"), float("nan"))
            if writer:
                writer.writerow([f"{t:.4f}", tid, cls, f"{xm:.3f}", f"{ym:.3f}", f"{vx:.3f}", f"{vy:.3f}"])
            else:
                print(f"{t:9.3f} {cls:7s} id={tid:<3} ({xm:5.2f},{ym:5.2f}) m  v=({vx:+.2f},{vy:+.2f}) m/s")
    if fitting:
        print(f"floor map refitted {n_fits} times")
    if machine_by == "marker" and n_frames:
        print(f"machine found in {100 * (n_marker + n_blob) / n_frames:.0f}% of frames "
              f"(marker {100 * n_marker / n_frames:.0f}%, blob fallback {100 * n_blob / n_frames:.0f}%)")
        print(f"worker found in {100 * (n_hat + n_box) / n_frames:.0f}% of frames "
              f"(hardhat marker {100 * n_hat / n_frames:.0f}%, person box {100 * n_box / n_frames:.0f}%)")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("source", nargs="?", default="0", help="video file, or camera index")
    p.add_argument("out_csv", nargs="?", help="tracks CSV to write (omit to print)")
    p.add_argument("--machine", choices=("marker", "yolo"), default="marker",
                   help="find the machine by ArUco marker 20 (default) or by YOLO car/truck boxes")
    a = p.parse_args()
    run(int(a.source) if a.source.isdigit() else a.source, a.out_csv, a.machine)
