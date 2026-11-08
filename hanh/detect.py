import cv2, numpy as np, time

cap = cv2.VideoCapture(0)  # on Windows you can try cv2.CAP_DSHOW as a 2nd arg if it opens slowly
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # needed for 1080p@30 on most webcams
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

print("opened:", cap.isOpened())

d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
p = cv2.aruco.DetectorParameters()
p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
det = cv2.aruco.ArucoDetector(d, p)

times, printed_shape = [], False
while True:
    ok, frame = cap.read()
    if not ok:
        print("Camera read failed"); break
    if not printed_shape:
        print("frame.shape:", frame.shape)   # want (1080, 1920, 3)
        printed_shape = True

    # rolling fps over the last 100 frames
    times.append(time.time()); times = times[-100:]
    fps = (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 else 0

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, rejected = det.detectMarkers(gray)

    if rejected:  # red = things that looked like markers but failed
        cv2.aruco.drawDetectedMarkers(frame, rejected, None, (0, 0, 255))
    if ids is not None:
        cv2.aruco.drawDetectedMarkers(frame, corners, ids)
        for c, i in zip(corners, ids.flatten()):
            c = c.reshape(4, 2)
            side = np.linalg.norm(c[0] - c[1])          # marker size in pixels
            ctr = c.mean(axis=0)
            top = (c[0] + c[1]) / 2                     # middle of printed TOP edge
            cv2.arrowedLine(frame, tuple(ctr.astype(int)), tuple(top.astype(int)), (255, 0, 0), 3)
            cv2.putText(frame, f"{int(side)}px", tuple((c[2] + [0, 25]).astype(int)),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)

    cv2.putText(frame, f"{frame.shape[1]}x{frame.shape[0]}  {fps:.1f} fps  "
                f"found={0 if ids is None else len(ids)} rejected={len(rejected)}",
                (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 255, 0), 3)
    cv2.imshow("aruco", cv2.resize(frame, None, fx=0.5, fy=0.5))
    if cv2.waitKey(1) == 27:
        break

cap.release(); cv2.destroyAllWindows()