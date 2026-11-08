"""Floor-mapping check: 4 markers to fit the map, 1 held back to test it.

Done when (next-steps plan, page 3):
  * every marker is found in 20 of 20 frames, and
  * the held-out marker lands within about 10 cm of its tape-measured spot.

How to use:
  1. Tape the 5 markers flat on the floor. Measure the CENTRE of each one in
     floor metres (origin = marker 0, +x toward marker 1, +y toward marker 3)
     and fill in FLOOR below. The held-out marker goes somewhere inside the
     square, NOT on the line between two corners.
  2. Live camera:      python floor_check.py        (SPACE = run a check, Esc = quit)
     Recorded video:   python floor_check.py clip.mp4   (checks the first 20 frames)
  3. Read the report in the terminal. It is also saved to floor_check.json.
  Re-run after any camera bump.
"""
import json, sys, time
import cv2, numpy as np

# ---- YOUR TAPE-MEASURE NUMBERS (metres, centre of each marker) --------
FLOOR = {
    0: (0.00, 0.00),
    1: (1.50, 0.00),
    2: (1.50, 0.80),
    3: (0.00, 0.80),
    4: (0.20, 0.20),          # held-out marker: measure it and put (x, y) here
}
FIT_IDS = [0, 1, 2, 3]   # used to build the map
CHECK_ID = 4             # never used to build the map, only to test it
N_FRAMES = 20
PASS_CM = 10.0
# ------------------------------------------------------------------------

W, HGT = 1280, 720
ALL_IDS = FIT_IDS + [CHECK_ID]

d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
p = cv2.aruco.DetectorParameters()
p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
det = cv2.aruco.ArucoDetector(d, p)


def detect(frame):
    """{marker id: pixel centre} for the markers we care about."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = det.detectMarkers(gray)
    if ids is None:
        return {}, corners, ids
    seen = {int(i): c.reshape(4, 2).mean(axis=0)
            for c, i in zip(corners, ids.flatten()) if int(i) in ALL_IDS}
    return seen, corners, ids


def fit(pix, ids):
    """Homography pixels -> floor metres from the markers in `ids`."""
    src = np.float32([pix[k] for k in ids])
    dst = np.float32([FLOOR[k] for k in ids])
    H, _ = cv2.findHomography(src, dst)
    return H


def to_floor(H, uv):
    return cv2.perspectiveTransform(np.float32(uv).reshape(-1, 1, 2), H).reshape(-1, 2)


def err_cm(H, mid, pix):
    return float(np.linalg.norm(to_floor(H, pix)[0] - np.array(FLOOR[mid])) * 100)


def check(frames_seen):
    """frames_seen: list (one per frame) of {id: pixel centre}. Returns the report."""
    counts = {m: sum(m in s for s in frames_seen) for m in ALL_IDS}

    # held-out error, one number per frame where all 5 were seen
    per_frame = [err_cm(fit(s, FIT_IDS), CHECK_ID, s[CHECK_ID])
                 for s in frames_seen if all(m in s for m in ALL_IDS)]

    # average pixel centre of each marker over the frames it was seen in
    mean_pix = {m: np.mean([s[m] for s in frames_seen if m in s], axis=0)
                for m in ALL_IDS if counts[m]}

    # jitter: how much the held-out point moves frame to frame (mm)
    jitter_mm = None
    if len(per_frame) >= 2:
        pts = [to_floor(fit(s, FIT_IDS), s[CHECK_ID])[0]
               for s in frames_seen if all(m in s for m in ALL_IDS)]
        jitter_mm = round(float(np.linalg.norm(np.std(pts, axis=0)) * 1000), 1)

    all_found = all(counts[m] == len(frames_seen) for m in ALL_IDS)
    worst = max(per_frame) if per_frame else None
    passed = bool(all_found and worst is not None and worst <= PASS_CM)

    H = fit(mean_pix, FIT_IDS) if all(m in mean_pix for m in FIT_IDS) else None
    return {
        "v": 1,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "frames": len(frames_seen),
        "found": {str(m): counts[m] for m in ALL_IDS},
        "held_out_id": CHECK_ID,
        "held_out_err_cm": None if worst is None else {
            "median": round(float(np.median(per_frame)), 1),
            "worst": round(worst, 1)},
        "jitter_mm": jitter_mm,
        "pass_cm": PASS_CM,
        "passed": passed,
        "H": None if H is None else H.tolist(),
        "floor_markers_m": {str(k): v for k, v in FLOOR.items()},
        "image_size": [W, HGT],
    }


def print_report(r):
    n = r["frames"]
    print("\n==== floor check ====")
    for m, c in r["found"].items():
        tag = "  <-- held out" if int(m) == CHECK_ID else ""
        print(f"  marker {m}: found {c}/{n}{'' if c == n else '  MISSING SOME'}{tag}")
    e = r["held_out_err_cm"]
    if e:
        print(f"  held-out error: median {e['median']} cm, worst {e['worst']} cm "
              f"(target <= {PASS_CM:.0f} cm)")
        print(f"  jitter: {r['jitter_mm']} mm")
    else:
        print("  held-out error: not measured (need all 5 markers in the same frame)")
    print("  RESULT:", "PASS" if r["passed"] else "FAIL")
    json.dump(r, open("floor_check.json", "w"), indent=2)
    print("  saved floor_check.json\n")


def main():
    if any(FLOOR[m] is None for m in ALL_IDS):
        sys.exit(f"Fill in FLOOR[{CHECK_ID}] (and check 0-3) with tape-measured metres first.")

    video = sys.argv[1] if len(sys.argv) > 1 else None
    cap = cv2.VideoCapture(video if video else 0)
    if not video:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HGT)

    if video:   # recorded clip: take the first N frames, no window
        frames = []
        while len(frames) < N_FRAMES:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(detect(frame)[0])
        cap.release()
        if len(frames) < N_FRAMES:
            print(f"warning: clip only had {len(frames)} frames")
        print_report(check(frames))
        return

    collecting = None   # list while a check is running
    while True:
        ok, frame = cap.read()
        if not ok:
            print("Camera read failed"); break
        seen, corners, ids = detect(frame)
        if ids is not None:
            cv2.aruco.drawDetectedMarkers(frame, corners, ids)

        if collecting is not None:
            collecting.append(seen)
            if len(collecting) == N_FRAMES:
                print_report(check(collecting))
                collecting = None

        status = "  ".join(f"{m}:{'ok' if m in seen else '--'}" for m in ALL_IDS)
        msg = f"collecting {len(collecting)}/{N_FRAMES}" if collecting is not None \
            else "SPACE = check   Esc = quit"
        cv2.putText(frame, status, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1,
                    (0, 255, 0) if len(seen) == len(ALL_IDS) else (0, 0, 255), 2)
        cv2.putText(frame, msg, (20, HGT - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (255, 255, 255), 2)
        cv2.imshow("floor check", frame)

        k = cv2.waitKey(1) & 0xFF
        if k == 27:
            break
        if k == ord(" ") and collecting is None:
            collecting = []

    cap.release(); cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
