import cv2
d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
for mid in [0, 1, 2, 3, 4, 10, 11, 20, 21, 30, 31, 32, 33]:
    img = cv2.aruco.generateImageMarker(d, mid, 800)
    img = cv2.copyMakeBorder(img, 120, 120, 120, 120, cv2.BORDER_CONSTANT, value=255)
    cv2.putText(img, f"ID {mid} TOP", (130, 90), cv2.FONT_HERSHEY_SIMPLEX, 2, 0, 3)
    cv2.imwrite(f"marker_{mid:02d}.png", img)