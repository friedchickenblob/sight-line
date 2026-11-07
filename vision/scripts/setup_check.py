"""Check a camera position BEFORE filming: are the floor markers and the chair's marker seen, and big enough?

Usage: python scripts/setup_check.py 0              # live camera (index 0); press q to stop
       python scripts/setup_check.py clips/A.mp4    # or check a recorded clip

Walk the chair (marker 20 on top) to every corner of the taped area, the far side especially, while this
runs. At the end it prints, for each marker: how often it was seen and its size in pixels.

Rules of thumb (ArUco 4x4):
  shortest side >= 25 px  -> reliable
  15-25 px                -> works but flickers; move the camera closer/higher or print bigger
  < 15 px                 -> too small
If clips/floor.json exists and markers 0-3 are seen, the window also shows the chair's floor position in metres.
"""
import sys
from collections import defaultdict
import cv2
import numpy as np
import floor_map

FLOOR_IDS = floor_map.FLOOR_IDS
CHAIR_ID, HARDHAT_ID = 20, 10
GOOD_PX, MIN_PX = 25, 15


def open_source(src):
    if src.isdigit():
        from record_clips import open_cam
        return open_cam(int(src)), True
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise SystemExit(f"can't open {src}")
    return cap, False


def main(src):
    cap, live = open_source(src)
    cfg = floor_map.load_config()
    sizes = defaultdict(list)  # id -> shortest side px, one per frame it was seen
    n, H = 0, None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        n += 1
        found = floor_map.detect(frame)
        for i, c in found.items():
            sizes[i].append(floor_map.side_px(c))
        if cfg and all(i in found for i in FLOOR_IDS):
            H, _ = floor_map.fit_from_frame(frame, cfg["markers_m"])

        # overlay
        for i, c in found.items():
            px = floor_map.side_px(c)
            col = (0, 200, 0) if px >= GOOD_PX else (0, 200, 255) if px >= MIN_PX else (0, 0, 255)
            cv2.polylines(frame, [c.astype(np.int32)], True, col, 2)
            label = f"{i}: {px:.0f}px"
            if i == CHAIR_ID and H is not None:
                xy, = floor_map.to_floor(H, [c.mean(axis=0)])
                xy = floor_map.parallax_fix(xy, cfg.get("machine_marker_height_m", 0.0), cfg)
                label += f"  ({xy[0]:.2f}, {xy[1]:.2f}) m"
            cv2.putText(frame, label, tuple(int(v) for v in c[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
        missing = [i for i in (*FLOOR_IDS, CHAIR_ID) if i not in found]
        cv2.putText(frame, f"missing: {missing}" if missing else "all markers seen", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255) if missing else (0, 200, 0), 2)
        cv2.imshow("setup check (q to stop)", cv2.resize(frame, None, fx=0.75, fy=0.75) if frame.shape[1] > 1600 else frame)
        if cv2.waitKey(1 if live else 1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()
    report(sizes, n, cfg)


def report(sizes, n, cfg):
    if n == 0:
        raise SystemExit("no frames read")
    print(f"\n{n} frames\n")
    print(f"{'marker':<14}{'seen':>7}{'min px':>9}{'median px':>11}  verdict")
    problems = []
    names = {**{i: f"floor {i}" for i in FLOOR_IDS}, CHAIR_ID: "chair 20", HARDHAT_ID: "hardhat 10"}
    for i in sorted(set(sizes) | set(FLOOR_IDS) | {CHAIR_ID}):
        s = sizes.get(i, [])
        name = names.get(i, f"other {i}")
        if not s:
            print(f"{name:<14}{0:>6}%{'-':>9}{'-':>11}  NEVER SEEN")
            if i in names and i != HARDHAT_ID:
                problems.append(f"{name} never seen")
            continue
        seen, lo, med = 100 * len(s) / n, min(s), float(np.median(s))
        if lo >= GOOD_PX:
            v = "OK"
        elif med >= GOOD_PX:
            v = "OK, but small at its worst spot"
        elif med >= MIN_PX:
            v = "MARGINAL"
        else:
            v = "TOO SMALL"
        print(f"{name:<14}{seen:>6.0f}%{lo:>9.0f}{med:>11.0f}  {v}")
        if i in FLOOR_IDS and seen < 90:
            problems.append(f"{name} seen in only {seen:.0f}% of frames (it shouldn't move: check glare/blocking)")
        if v in ("MARGINAL", "TOO SMALL"):
            problems.append(f"{name} is {med:.0f} px: print it bigger or move the camera closer/higher")
    if not cfg:
        print(f"\n(no {floor_map.CONFIG}: copy clips/floor.example.json and fill in the measured positions "
              "to see floor positions in metres)")
    print()
    for p in problems:
        print("PROBLEM:", p)
    if not problems:
        print("Setup looks good. Note: the chair is only checked where you moved it, so cover the far corners.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "0")
