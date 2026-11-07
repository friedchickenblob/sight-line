"""Convert a phone video into a test clip and check it (RECORDING-PLAN.md, part 5).

Usage (run from vision/):
  python scripts/prep_clip.py ~/sightline-raw/IMG_1234.MOV A    # -> clips/A.mp4, then checks it
  python scripts/prep_clip.py --check clips/A.mp4                # only check an existing clip

Phones record a varying frame rate. The output is a constant 30 fps, so frame index / 30 is the
real time (track.py relies on this). Uses ffmpeg if installed (brew install ffmpeg), else OpenCV.

The check reports resolution, fps, length, and which ArUco markers (DICT_4X4_50) are visible, with
their size in pixels: floor 0-3 must all be there; under ~20-30 px a marker won't read reliably.
"""
import argparse, pathlib, shutil, subprocess
import cv2
import numpy as np

FPS = 30.0
FLOOR_IDS, HARDHAT_ID, TRUCK_ID = (0, 1, 2, 3), 10, 20
NAMES = {**{i: f"floor {i}" for i in FLOOR_IDS}, HARDHAT_ID: "hardhat", TRUCK_ID: "truck"}


def convert(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", f"fps={FPS:g}",
                        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dst)], check=True)
        return
    # OpenCV fallback: for each output tick (every 1/30 s), write the latest frame at or before it.
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise SystemExit(f"can't open {src} (install ffmpeg: brew install ffmpeg)")
    out, tick, last = None, 0, None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if out is None:
            h, w = frame.shape[:2]
            out = cv2.VideoWriter(str(dst), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (w, h))
        while tick / FPS < t and last is not None:
            out.write(last); tick += 1
        last = frame
    if last is not None:
        out.write(last)
    cap.release()
    if out is None:
        raise SystemExit(f"no frames read from {src}")
    out.release()


def check(path, samples=30):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"can't open {path}")
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps, n = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"{path}: {w}x{h}, {fps:.2f} fps, {n} frames, {n / max(fps, 1e-9):.1f} s")
    problems = []
    if abs(fps - FPS) > 0.01:
        problems.append(f"fps is {fps:.2f}, not {FPS:g}: convert it with this script first")
    if w < h:
        problems.append("portrait video: record in landscape")

    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),
                                  cv2.aruco.DetectorParameters())
    seen = {}  # id -> list of side lengths in px, over the sampled frames
    for idx in np.linspace(0, max(n - 1, 0), samples).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, frame = cap.read()
        if not ok:
            continue
        corners, ids, _ = det.detectMarkers(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        for c, i in zip(corners, [] if ids is None else ids.flatten()):
            c = c.reshape(4, 2)
            seen.setdefault(int(i), []).append(np.linalg.norm(c - np.roll(c, 1, axis=0), axis=1).mean())
    cap.release()

    print(f"ArUco markers in {samples} frames spread over the clip:")
    for i in sorted(seen):
        px = np.median(seen[i])
        note = "  <- small, may not read reliably" if px < 30 else ""
        print(f"  ID {i:2d} ({NAMES.get(i, 'other')}): in {len(seen[i])}/{samples} frames, ~{px:.0f} px{note}")
    missing = [i for i in FLOOR_IDS if i not in seen]
    if missing:
        problems.append(f"floor markers {missing} never seen: the floor map can't be fitted")
    for p in problems:
        print("PROBLEM:", p)
    if not problems:
        print("OK")
    return not problems


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("src", help="phone video, or a clip with --check")
    p.add_argument("name", nargs="?", help="clip letter, e.g. A -> clips/A.mp4")
    p.add_argument("--check", action="store_true", help="only check src, don't convert")
    a = p.parse_args()
    if a.check:
        raise SystemExit(0 if check(a.src) else 1)
    if not a.name:
        p.error("give a clip letter, e.g. A")
    dst = pathlib.Path("clips") / f"{a.name}.mp4"
    convert(pathlib.Path(a.src).expanduser(), dst)
    raise SystemExit(0 if check(dst) else 1)
