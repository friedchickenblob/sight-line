"""Head direction (yaw) and position from marker 10 (guide page 7).

How to use:
  1. Same camera setup as floor_map.py: fixed, looking down, markers 0-3 in view.
  2. Fill in the settings below (nadir, camera height, marker height).
  3. python head.py
  4. Marker 10 shows an arrow, its yaw in degrees, and its floor position.
Keys:
  c   calibrate: point marker 10's TOP exactly toward marker 1 (+x), press c.
      It averages the last ~1 s and sets OFFSET so that reads 0 deg.
  Esc quit
"""
from collections import deque
import cv2, numpy as np

# ---- YOUR SETTINGS ----------------------------------------------------
FLOOR = {                     # tape-measure centres of floor markers (m)
    0: (0.00, 0.00),
    1: (1.50, 0.00),
    2: (1.50, 0.80),
    3: (0.00, 0.80),
}
NADIR = (0.85, 0.60)  # floor point straight under the lens (m): hang a string from the lens
CAM_HEIGHT_M = 2.50   # lens to floor (m)
MARKER_H_M = 1.57     # height of marker 10 above the floor (m): 0 = on the floor
HEAD_ID = 10
OFFSET = 3.5         # deg, set by pressing c
# -----------------------------------------------------------------------

W, HGT = 1280, 720

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HGT)

d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
p = cv2.aruco.DetectorParameters()
p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
det = cv2.aruco.ArucoDetector(d, p)


def wrap(deg):
    """Wrap an angle to [-180, 180)."""
    return (deg + 180.0) % 360.0 - 180.0


def to_floor(H, uv):
    pts = np.float32(uv).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, H).reshape(-1, 2)


def fit_floor(corners, ids):
    seen = {int(i): c.reshape(4, 2).mean(axis=0)
            for c, i in zip(corners, ids.flatten()) if int(i) in FLOOR}
    if len(seen) < 4:
        return None
    keys = sorted(seen)
    H, _ = cv2.findHomography(np.float32([seen[k] for k in keys]),
                              np.float32([FLOOR[k] for k in keys]))
    return H


def head_pose(H, c, h=MARKER_H_M, offset=0.0):
    """Yaw (deg, 0 = +x, CCW positive) and floor position (m) of a marker at height h."""
    c = c.reshape(4, 2)                    # TL, TR, BR, BL in pixels
    top = (c[0] + c[1]) / 2                # middle of the printed TOP edge
    bot = (c[3] + c[2]) / 2
    f_top, f_bot, f_mid = to_floor(H, [top, bot, c.mean(axis=0)])
    dx, dy = f_top - f_bot
    yaw = wrap(np.degrees(np.arctan2(dy, dx)) + offset)
    N = np.array(NADIR)
    pos = N + (f_mid - N) * (CAM_HEIGHT_M - h) / CAM_HEIGHT_M   # undo height error
    return yaw, pos


def circ_mean(deg):
    r = np.radians(deg)
    return np.degrees(np.arctan2(np.sin(r).mean(), np.cos(r).mean()))


H = None
raw_yaws = deque(maxlen=30)   # last ~1 s of yaw without offset, for calibration

while True:
    ok, frame = cap.read()
    if not ok:
        print("Camera read failed"); break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = det.detectMarkers(gray)

    if ids is not None:
        cv2.aruco.drawDetectedMarkers(frame, corners, ids)
        new_H = fit_floor(corners, ids)
        if new_H is not None:
            H = new_H

    if H is None:
        cv2.putText(frame, "waiting for floor markers 0-3", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
    elif ids is not None and HEAD_ID in ids.flatten():
        c = corners[list(ids.flatten()).index(HEAD_ID)].reshape(4, 2)
        raw_yaw, _ = head_pose(H, c)
        raw_yaws.append(raw_yaw)
        yaw, pos = head_pose(H, c, offset=OFFSET)

        ctr = c.mean(axis=0)
        top = (c[0] + c[1]) / 2
        tip = ctr + 2.5 * (top - ctr)      # long arrow in the facing direction
        cv2.arrowedLine(frame, tuple(ctr.astype(int)), tuple(tip.astype(int)),
                        (255, 0, 0), 4, tipLength=0.25)
        cv2.putText(frame, f"yaw {yaw:+.0f} deg   pos {pos[0]:.2f}, {pos[1]:.2f} m",
                    (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 0), 3)
    else:
        cv2.putText(frame, f"marker {HEAD_ID} not seen", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 165, 255), 2)

    cv2.putText(frame, f"offset {OFFSET:+.1f} deg   c=calibrate  Esc=quit",
                (20, HGT - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.imshow("head", frame)

    k = cv2.waitKey(1) & 0xFF
    if k == 27:
        break
    if k == ord("c") and len(raw_yaws) >= 10:
        OFFSET = -circ_mean(list(raw_yaws))
        print(f"OFFSET = {OFFSET:.1f}  (put this in the settings to keep it)")

cap.release(); cv2.destroyAllWindows()
