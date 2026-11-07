import cv2, numpy as np
from PIL import Image

DPI = 300
PAGE_W, PAGE_H = 2480, 3508      # A4 at 300 dpi. For US Letter use 2550, 3300
SIZES_CM = {0: 14, 1: 14, 2: 14, 3: 14, 10: 9, 20: 7}   # black-square size in cm

d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
pages = []
for mid, cm in SIZES_CM.items():
    cell = round(cm / 2.54 * DPI / 6)        # a marker is 6 cells wide
    black = cell * 6
    m = cv2.aruco.generateImageMarker(d, mid, black)
    m = np.pad(m, int(cell * 1.2), constant_values=255)   # white border

    page = np.full((PAGE_H, PAGE_W), 255, np.uint8)
    y0 = (PAGE_H - m.shape[0]) // 2
    x0 = (PAGE_W - m.shape[1]) // 2
    page[y0:y0 + m.shape[0], x0:x0 + m.shape[1]] = m

    label = f"ID {mid}  |  TOP edge = up on this page  |  black square {cm} cm"
    cv2.putText(page, label, (150, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.6, 0, 3)
    pages.append(Image.fromarray(page))

pages[0].save("markers.pdf", save_all=True, append_images=pages[1:], resolution=DPI)
print("saved markers.pdf")