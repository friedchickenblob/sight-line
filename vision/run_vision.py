"""Live person + vehicle detection, mapped onto the floor, with hazard direction from the worker.

    python -m vision.run_vision                                    # webcam 0, pixels only (no floor map)
    python -m vision.run_vision --layout floor_layout.json         # calibrate live from floor markers
    python -m vision.run_vision --calib floor_calib.json --layout floor_layout.json   # saved calibration
    python -m vision.run_vision --video clip.mp4                   # recorded video instead of the camera

Without a floor calibration you get boxes, track IDs and ground points, but no metres or angles.
With one, each vehicle gets distance, bearing (floor frame: 0 = +x, CCW +) and bearing relative
to the worker's head (0 ahead, +90 left, -90 right, -180 behind).

Worker = the tracked person (yellow); the head yaw is simulated with a / d until the Muse is
wired in. Keys: a / d head yaw +-15 deg, f freeze/unfreeze live calibration, s save calibration,
q / Esc quit.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2

from vision.detector import VEHICLE_LABELS, Detector
from vision.floor import FloorLayout, FloorMap, calibrate
from vision.overlay import GREEN, ORANGE, RED, WHITE, YELLOW, draw_grid, draw_head_arrow, label, pt
from vision.scene import WorkerSelector, build_scene

WIN = "SightLine vision"


def open_source(args):
    if args.video:
        cap = cv2.VideoCapture(args.video)
    else:
        cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        raise SystemExit(f"can't open {'video ' + args.video if args.video else f'camera {args.camera}'}")
    return cap


def draw_scene(view, scene, fm, yaw):
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
        txt = f"{d.label} #{d.track_id} {d.conf:.2f}"
        if r.distance_m is not None:
            txt += f"  {r.distance_m:.2f} m  brg {r.bearing_deg:+.0f}  rel {r.rel_bearing_deg:+.0f}"
            cv2.line(view, pt(w.det.ground_px), pt(d.ground_px), RED, 2, cv2.LINE_AA)
        label(view, txt, (x1, y1 - 6), ORANGE)


def summary(scene) -> str:
    w = scene.worker
    s = "worker: none" if not w else (
        f"worker #{w.det.track_id} " + (f"at ({w.floor_xy[0]:.2f}, {w.floor_xy[1]:.2f}) m" if w.floor_xy else "(no floor map)"))
    for r in scene.hazards:
        d = r.hazard.det
        s += f" | {d.label} #{d.track_id}"
        if r.distance_m is not None:
            s += f": {r.distance_m:.2f} m, brg {r.bearing_deg:+.0f}, rel {r.rel_bearing_deg:+.0f}"
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--video", help="video file instead of the camera")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--model", default="yolo26n.pt", help="YOLO weights (downloaded on first use)")
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--vehicles", nargs="+", default=sorted(VEHICLE_LABELS), help="COCO classes treated as vehicles")
    ap.add_argument("--no-track", action="store_true", help="detection only, no ByteTrack IDs")
    ap.add_argument("--layout", help="floor_layout.json: calibrate live from floor markers")
    ap.add_argument("--calib", help="floor_calib.json saved by check_floor / this tool (fixed calibration)")
    ap.add_argument("--save", default="floor_calib.json")
    ap.add_argument("--head-yaw", type=float, default=0.0)
    ap.add_argument("--print-every", type=float, default=1.0, help="console summary interval (s), 0 = off")
    args = ap.parse_args()

    layout = FloorLayout.load(args.layout) if args.layout else None
    fm = FloorMap.load(args.calib) if args.calib else None
    frozen = fm is not None
    det = Detector(args.model, args.conf, args.imgsz, not args.no_track, args.vehicles)
    selector = WorkerSelector()
    cap = open_source(args)
    yaw = args.head_yaw

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    print(__doc__)
    last_print, fps, t_prev = 0.0, 0.0, time.perf_counter()
    while True:
        ok, frame = cap.read()
        if not ok:
            print("end of video" if args.video else "camera read failed")
            break

        calib_status = "no floor map (pixels only)"
        if layout is not None and not frozen:
            try:
                fm, missing = calibrate(frame, layout)
                calib_status = f"live calib, missing {missing}" if missing else "live calib, all markers"
            except ValueError:
                calib_status = "live calib: markers not seen" + (" (using last)" if fm else "")
        elif fm is not None:
            calib_status = "calibration fixed"

        t0 = time.perf_counter()
        dets = det(frame)
        infer_ms = (time.perf_counter() - t0) * 1000
        scene = build_scene(dets, fm, yaw, selector)

        view = frame.copy()
        if fm is not None:
            draw_grid(view, fm, layout)
        draw_scene(view, scene, fm, yaw)

        now = time.perf_counter()
        fps = 0.9 * fps + 0.1 / max(now - t_prev, 1e-6) if fps else 1 / max(now - t_prev, 1e-6)
        t_prev = now
        label(view, f"{fps:4.1f} FPS  YOLO {infer_ms:3.0f} ms | {calib_status} | head yaw {yaw:+.0f} (a/d) | f freeze  s save  q quit",
              (10, 25), WHITE, 0.5)
        cv2.imshow(WIN, view)
        if args.print_every and now - last_print > args.print_every:
            print(summary(scene), flush=True)
            last_print = now

        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            break
        if k == ord("a"):
            yaw = (yaw + 15 + 180) % 360 - 180
        elif k == ord("d"):
            yaw = (yaw - 15 + 180) % 360 - 180
        elif k == ord("f") and fm is not None:
            frozen = not frozen
        elif k == ord("s") and fm is not None:
            fm.save(args.save, layout=args.layout)
            print("saved", Path(args.save).resolve())

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
