"""Live floor-mapping check: point the (angled) camera at the floor markers and verify
that pixels map to the right floor positions and angles.

    python -m vision.check_floor --layout floor_layout.json             # webcam 0
    python -m vision.check_floor --layout floor_layout.json --camera 1
    python -m vision.check_floor --layout floor_layout.json --image photo.jpg

What you see
    - each detected marker, outlined, with its fit error in cm
    - a 0.5 m grid and the +x (red) / +y (green) axes drawn ON the floor: if the grid
      lines don't follow the floor/tape lines, the calibration is wrong
Mouse
    left click      print the floor position (x, y) of that pixel; if a worker point is set,
                    also distance, bearing (floor frame) and bearing relative to the head
    right click     set the "worker" point (click where the person's FEET are)
Keys
    a / d           rotate the simulated head yaw by +15 / -15 deg (stand-in for the Muse)
    f               freeze / unfreeze calibration (stop re-fitting every frame)
    s               save the calibration to --save (default floor_calib.json)
    c               clear clicked points
    q / Esc         quit
"""

from __future__ import annotations

import argparse

import cv2
import numpy as np

from vision.floor import FloorLayout, calibrate, detect_markers
from vision.hazards import bearing_deg, distance_m, relative_bearing_deg
from vision.overlay import BLUE, RED, WHITE, YELLOW, draw_grid, draw_head_arrow, label

WIN = "SightLine floor check"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--layout", default="floor_layout.json")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--image", help="use a still image instead of the camera")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--save", default="floor_calib.json")
    ap.add_argument("--head-yaw", type=float, default=0.0, help="initial simulated head yaw (deg)")
    args = ap.parse_args()

    layout = FloorLayout.load(args.layout)
    cap = None
    if args.image:
        still = cv2.imread(args.image)
        if still is None:
            raise SystemExit(f"can't read {args.image}")
    else:
        cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW) if hasattr(cv2, "CAP_DSHOW") else cv2.VideoCapture(args.camera)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
        if not cap.isOpened():
            raise SystemExit(f"can't open camera {args.camera}")

    state = {"fm": None, "frozen": False, "worker": None, "points": [], "yaw": args.head_yaw}

    def on_mouse(event, u, v, *_):
        fm = state["fm"]
        if fm is None or event not in (cv2.EVENT_LBUTTONDOWN, cv2.EVENT_RBUTTONDOWN):
            return
        xy = tuple(fm.to_floor([[u, v]])[0])
        if event == cv2.EVENT_RBUTTONDOWN:
            state["worker"] = ((u, v), xy)
            print(f"worker set at floor ({xy[0]:.3f}, {xy[1]:.3f}) m")
            return
        state["points"].append(((u, v), xy))
        msg = f"point at floor ({xy[0]:.3f}, {xy[1]:.3f}) m"
        if state["worker"]:
            w = state["worker"][1]
            b = bearing_deg(w, xy)
            msg += (f" | from worker: {distance_m(w, xy):.2f} m, bearing {b:+.1f} deg, "
                    f"relative to head {relative_bearing_deg(b, state['yaw']):+.1f} deg")
        print(msg)

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WIN, on_mouse)
    print(__doc__)

    while True:
        if cap is not None:
            ok, frame = cap.read()
            if not ok:
                print("camera read failed")
                break
        else:
            frame = still.copy()

        status = ""
        if not state["frozen"]:
            try:
                state["fm"], missing = calibrate(frame, layout)
                status = f"missing markers {missing}" if missing else "all markers seen"
            except ValueError as e:
                status = str(e)
        else:
            status = "calibration FROZEN"
        fm = state["fm"]
        view = frame.copy()

        found = detect_markers(frame, layout.dictionary)
        for mid, c in found.items():
            cv2.polylines(view, [c.astype(np.int32)], True, BLUE, 2)
            err = fm.errors_m.get(mid) if fm else None
            txt = f"#{mid}" + (f" err {err * 100:.1f} cm" if err is not None else " (not in layout)")
            label(view, txt, c[0] + (0, -8), YELLOW if err is not None and err > 0.03 else WHITE)

        if fm is not None:
            draw_grid(view, fm, layout)
            if state["worker"]:
                (wu, wv), w = state["worker"]
                cv2.circle(view, (wu, wv), 8, YELLOW, -1)
                draw_head_arrow(view, fm, w, state["yaw"])
                label(view, f"worker ({w[0]:.2f}, {w[1]:.2f}) head {state['yaw']:+.0f} deg", (wu + 10, wv + 20), YELLOW)
            for (u, v), xy in state["points"][-8:]:
                cv2.circle(view, (u, v), 6, RED, -1)
                text = f"({xy[0]:.2f}, {xy[1]:.2f})"
                if state["worker"]:
                    (wu, wv), w = state["worker"]
                    cv2.line(view, (wu, wv), (u, v), RED, 1, cv2.LINE_AA)
                    b = bearing_deg(w, xy)
                    text += f" {distance_m(w, xy):.2f} m  brg {b:+.0f}  rel {relative_bearing_deg(b, state['yaw']):+.0f}"
                label(view, text, (u + 10, v - 10))

        label(view, f"{status} | right-click = worker, left-click = point, a/d = head yaw, f = freeze, s = save, q = quit",
              (10, 25), WHITE, 0.5)
        cv2.imshow(WIN, view)

        k = cv2.waitKey(30) & 0xFF
        if k in (ord("q"), 27):
            break
        if k == ord("f"):
            state["frozen"] = not state["frozen"] and fm is not None
        elif k == ord("s") and fm is not None:
            fm.save(args.save, layout=args.layout)
            print("saved", args.save, "errors (cm):", {i: round(e * 100, 1) for i, e in fm.errors_m.items()})
        elif k == ord("c"):
            state["points"].clear()
            state["worker"] = None
        elif k == ord("a"):
            state["yaw"] = (state["yaw"] + 15 + 180) % 360 - 180
        elif k == ord("d"):
            state["yaw"] = (state["yaw"] - 15 + 180) % 360 - 180

    if cap is not None:
        cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
