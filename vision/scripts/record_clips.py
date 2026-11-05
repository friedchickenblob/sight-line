"""Record test clips A–G (guide page 3).

Usage: python scripts/record_clips.py A 60 --cam 0
"""
import argparse, platform, time
import cv2


def open_cam(i=0, w=1920, h=1080):
    backend = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
    cap = cv2.VideoCapture(i, backend)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # needed for 1080p30 on most webcams
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    return cap


def record(name, seconds=60, cam=0):
    cap = open_cam(cam)
    ok, f = cap.read()
    if not ok:
        raise SystemExit("camera not readable")
    h, w = f.shape[:2]
    out = cv2.VideoWriter(f"clips/{name}.mp4", cv2.VideoWriter_fourcc(*"mp4v"), 30, (w, h))
    end = time.time() + seconds
    while time.time() < end:
        ok, f = cap.read()
        if ok:
            out.write(f)
            cv2.imshow("rec", f)
            if cv2.waitKey(1) == 27:  # Esc stops early
                break
    out.release(); cap.release(); cv2.destroyAllWindows()
    print(f"saved clips/{name}.mp4 ({w}x{h}) — note camera height + lens in clips/README.md")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("name"); p.add_argument("seconds", type=int, nargs="?", default=60)
    p.add_argument("--cam", type=int, default=0)
    a = p.parse_args()
    record(a.name, a.seconds, a.cam)
