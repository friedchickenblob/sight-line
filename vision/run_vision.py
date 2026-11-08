"""Live person + vehicle detection, mapped onto the floor, with hazard direction from the worker.

    python -m vision.run_vision                                    # webcam 0, pixels only (no floor map)
    python -m vision.run_vision --layout floor_layout.json         # calibrate live from floor markers
    python -m vision.run_vision --calib floor_calib.json --layout floor_layout.json   # saved calibration
    python -m vision.run_vision --video clip.mp4                   # recorded video instead of the camera
    python -m vision.run_vision --video clip.mp4 --layout floor_layout.json --car-marker 20 --worker-marker 10

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
from vision.floor import FloorLayout, FloorMap
from vision.pipeline import VisionPipeline, draw_scene, summary  # noqa: F401  (draw_scene/summary re-exported)

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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--video", help="video file instead of the camera")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--model", default="yolo26n.pt", help="YOLO weights (downloaded on first use)")
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--vehicle-conf", type=float, default=0.25, help="YOLO threshold for vehicles")
    ap.add_argument("--car-marker", type=int, action="append", default=[], metavar="ID",
                    help="ArUco id taped on the toy car (repeatable)")
    ap.add_argument("--car-label", default="toy car")
    ap.add_argument("--worker-marker", type=int, metavar="ID",
                    help="ArUco id on top of the worker's hardhat: that person is the worker")
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
    det = Detector(args.model, args.conf, args.imgsz, not args.no_track, args.vehicles,
                   vehicle_conf=args.vehicle_conf)
    pipe = VisionPipeline(det, layout, fm, args.head_yaw,
                          hazard_markers={i: args.car_label for i in args.car_marker},
                          worker_marker=args.worker_marker)
    cap = open_source(args)

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    print(__doc__)
    last_print, fps, t_prev = 0.0, 0.0, time.perf_counter()
    while True:
        ok, frame = cap.read()
        if not ok:
            print("end of video" if args.video else "camera read failed")
            break

        res = pipe.process(frame)
        now = time.perf_counter()
        fps = 0.9 * fps + 0.1 / max(now - t_prev, 1e-6) if fps else 1 / max(now - t_prev, 1e-6)
        t_prev = now
        view = pipe.render(frame, res, f"{fps:4.1f} FPS  YOLO {res.infer_ms:3.0f} ms | {res.calib_status} | "
                                       f"head yaw {pipe.head_yaw_deg:+.0f} (a/d) | f freeze  s save  q quit")
        cv2.imshow(WIN, view)
        if args.print_every and now - last_print > args.print_every:
            print(summary(res.scene), flush=True)
            last_print = now

        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            break
        if k == ord("a"):
            pipe.nudge_yaw(15)
        elif k == ord("d"):
            pipe.nudge_yaw(-15)
        elif k == ord("f"):
            pipe.toggle_freeze()
        elif k == ord("s") and pipe.floor_map is not None:
            pipe.floor_map.save(args.save, layout=args.layout)
            print("saved", Path(args.save).resolve())

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
