"""Generate printable ArUco floor markers (one PNG per marker).

    python -m vision.make_markers                      # markers 0-3, DICT_4X4_50 -> data/markers/
    python -m vision.make_markers --ids 0 1 2 3 4 5 --out data/markers

Print each PNG at 100% / "actual size", then MEASURE the black square with a ruler and
put that size in floor_layout.json (marker_size_m). The white border around the black
square is part of the marker: don't trim it off.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from vision.floor import aruco_dictionary


def make(marker_id: int, dictionary: str, px: int) -> np.ndarray:
    m = cv2.aruco.generateImageMarker(aruco_dictionary(dictionary), marker_id, px)
    border = px // 6
    img = cv2.copyMakeBorder(m, border, border + 60, border, border, cv2.BORDER_CONSTANT, value=255)
    cv2.putText(img, f"ID {marker_id}  ({dictionary})  top edge -> +y", (border, img.shape[0] - 25),
                cv2.FONT_HERSHEY_SIMPLEX, px / 900, 0, 2, cv2.LINE_AA)
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--dictionary", default="DICT_4X4_50")
    ap.add_argument("--px", type=int, default=1200, help="pixel size of the black square")
    ap.add_argument("--out", default="data/markers")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for i in args.ids:
        path = out / f"marker_{i}.png"
        cv2.imwrite(str(path), make(i, args.dictionary, args.px))
        print("wrote", path)


if __name__ == "__main__":
    main()
