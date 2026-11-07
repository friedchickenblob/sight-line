"""Make a printable PDF of the ArUco markers (DICT_4X4_50), one per Letter page, at a real size.

Usage: python scripts/make_markers.py                      # markers 0 1 2 3 10 20 at 18 cm -> markers.pdf
       python scripts/make_markers.py --ids 20 --cm 15     # just the chair marker, 15 cm

Print at 100% / "Actual size" (NOT "fit to page"), then measure the black square with a ruler and write
the real size in the clip log. Keep the white border: the detector needs it.
"""
import argparse
import cv2
import numpy as np
from PIL import Image, ImageDraw

DPI = 300
PAGE_IN = (8.5, 11)  # US Letter; an 18 cm marker also fits on A4


def page(marker_id, cm):
    w, h = int(PAGE_IN[0] * DPI), int(PAGE_IN[1] * DPI)
    side = int(round(cm / 2.54 * DPI))
    dic = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    m = cv2.aruco.generateImageMarker(dic, marker_id, side)
    img = Image.new("L", (w, h), 255)
    x0, y0 = (w - side) // 2, (h - side) // 2
    img.paste(Image.fromarray(m), (x0, y0))
    d = ImageDraw.Draw(img)
    d.text((x0, y0 + side + 40), f"ArUco 4x4_50  id {marker_id}   black square = {cm:g} cm   (print at 100%)",
           fill=0)
    d.text((x0, y0 - 80), "^ this edge = marker's 'up' (keep the same for all floor markers)", fill=0)
    return img


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ids", type=int, nargs="+", default=[0, 1, 2, 3, 10, 20])
    p.add_argument("--cm", type=float, default=18.0, help="side of the black square in cm (max ~18 for Letter/A4)")
    p.add_argument("--out", default="markers.pdf")
    a = p.parse_args()
    if a.cm > 19:
        raise SystemExit("bigger than ~19 cm won't fit one page; print in parts or use a print shop")
    pages = [page(i, a.cm) for i in a.ids]
    pages[0].save(a.out, save_all=True, append_images=pages[1:], resolution=DPI)
    print(f"wrote {a.out}: ids {a.ids}, {a.cm:g} cm. Print at 100% and check the size with a ruler.")


if __name__ == "__main__":
    main()
