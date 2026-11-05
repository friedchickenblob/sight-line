"""Experiment 2 (guide page 6): capture/model ms and FPS on the demo laptop. Run plugged in.

Usage: python scripts/speed_test.py yolo26n.pt 640
       python scripts/speed_test.py yolo26n_openvino_model/ 480
Export first with: yolo export model=yolo26n.pt format=openvino   (or format=onnx)
"""
import sys, time
import numpy as np
from ultralytics import YOLO
from record_clips import open_cam

weights = sys.argv[1] if len(sys.argv) > 1 else "yolo26n.pt"
imgsz = int(sys.argv[2]) if len(sys.argv) > 2 else 640
model = YOLO(weights)
cap = open_cam()
times = []
for _ in range(300):
    t0 = time.perf_counter()
    ok, frame = cap.read()
    t1 = time.perf_counter()
    r = model.predict(frame, imgsz=imgsz, verbose=False)[0]
    t2 = time.perf_counter()
    times.append((t1 - t0, t2 - t1, r.speed["inference"] / 1000))
cap.release()
cap_s, total_s, inf_s = np.median(times[10:], axis=0)  # skip warm-up frames
print(f"{weights} @{imgsz}: capture {cap_s*1e3:.0f} ms | model {total_s*1e3:.0f} ms "
      f"(net {inf_s*1e3:.0f}) | {1/(cap_s+total_s):.1f} FPS")
