"""Turn camera pixels into floor metres.

How to use:
  1. Camera fixed, looking straight down, all 4 floor markers (0-3) in view.
  2. python floor_map.py
  3. Once it says "MAP READY", every other marker (e.g. 30) shows its
     floor position in metres. You can also click anywhere on the picture.
  4. Press s to save floor_calib.json, r to re-fit, Esc to quit.
"""
import json, time
import cv2, numpy as np

# ---- YOUR TAPE-MEASURE NUMBERS (metres, centre of each marker) ----
FLOOR = {
    0: (0.00, 0.00),
    1: (1.00, 0.00),
    2: (1.00, 0.50),
    3: (0.00, 0.50),
}
CAM_HEIGHT_M = 1.50     # lens to floor
MARKER_SIZE_M = 0.12    # printed black square, for the record
# --------------------------------------------------------------------

W, HGT = 1280, 720

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HGT)

d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
p = cv2.aruco.DetectorParameters()
p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
det = cv2.aruco.ArucoDetector(d, p)


def centre(c):
    return c.reshape(4, 2).mean(axis=0)


def to_floor(H, uv):
    """Pixel(s) -> floor metres. uv can be one (u, v) or a list of them."""
    pts = np.float32(uv).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, H).reshape(-1, 2)


def fit_floor(corners, ids):
    seen = {int(i): centre(c) for c, i in zip(corners, ids.flatten()) if int(i) in FLOOR}
    if len(seen) < 4:
        return None
    keys = sorted(seen)
    src = np.float32([seen[k] for k in keys])
    dst = np.float32([FLOOR[k] for k in keys])
    H, _ = cv2.findHomography(src, dst)
    return H


H = None
click = None  # last mouse click, in pixels


def on_mouse(event, x, y, flags, param):
    global click
    if event == cv2.EVENT_LBUTTONDOWN:
        click = (x, y)


cv2.namedWindow("floor map")
cv2.setMouseCallback("floor map", on_mouse)

while True:
    ok, frame = cap.read()
    if not ok:
        print("Camera read failed"); break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = det.detectMarkers(gray)

    if ids is not None:
        cv2.aruco.drawDetectedMarkers(frame, corners, ids)
        # re-fit every frame all 4 corners are seen, so a small camera
        # bump doesn't shift the map
        new_H = fit_floor(corners, ids)
        if new_H is not None:
            if H is None:
                print("MAP READY. H =\n", new_H)
            H = new_H

    if H is None:
        n = 0 if ids is None else sum(int(i) in FLOOR for i in ids.flatten())
        msg = f"waiting for floor markers 0-3: see {n}/4"
        cv2.putText(frame, msg, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
    else:
        cv2.putText(frame, "MAP READY  (s=save  r=refit  Esc=quit)", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        # every marker: show where the map puts it, in metres
        if ids is not None:
            for c, i in zip(corners, ids.flatten()):
                ctr = centre(c)
                x, y = to_floor(H, ctr)[0]
                cv2.putText(frame, f"ID{int(i)}: {x:.2f}, {y:.2f} m",
                            tuple((ctr + [-60, 50]).astype(int)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        # mouse click: show where that pixel is on the floor
        if click is not None:
            x, y = to_floor(H, click)[0]
            cv2.circle(frame, click, 6, (255, 0, 255), -1)
            cv2.putText(frame, f"{x:.2f}, {y:.2f} m", (click[0] + 10, click[1] - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 255), 2)

    cv2.imshow("floor map", frame)
    k = cv2.waitKey(1) & 0xFF
    if k == 27:
        break
    if k == ord("r"):
        H = None; print("re-fitting...")
    if k == ord("s") and H is not None:
        json.dump({"v": 1, "H": H.tolist(), "image_size": [W, HGT],
                   "floor_markers_m": {str(k): v for k, v in FLOOR.items()},
                   "cam_height_m": CAM_HEIGHT_M, "marker_size_m": MARKER_SIZE_M,
                   "created": time.strftime("%Y-%m-%d %H:%M:%S")},
                  open("floor_calib.json", "w"), indent=2)
        print("saved floor_calib.json")

cap.release(); cv2.destroyAllWindows()
