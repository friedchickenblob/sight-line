"""Drawing helpers shared by the vision tools (OpenCV, BGR colours)."""

from __future__ import annotations

import math

import cv2
import numpy as np

RED, GREEN, BLUE = (0, 0, 255), (0, 200, 0), (255, 120, 0)
YELLOW, ORANGE, WHITE, GREY = (0, 220, 255), (0, 140, 255), (255, 255, 255), (160, 160, 160)


def pt(xy) -> tuple[int, int]:
    return int(round(xy[0])), int(round(xy[1]))


def label(img, text, xy, color=WHITE, scale=0.55):
    """Text on a dark box so it reads on any background; xy = bottom-left of the text."""
    (w, h), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    x, y = pt(xy)
    x = max(0, min(x, img.shape[1] - w - 4))
    y = max(h + 4, y)
    cv2.rectangle(img, (x - 2, y - h - 4), (x + w + 2, y + base), (30, 30, 30), -1)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def draw_axes(img, fm, length_m=0.5):
    o, px, py = fm.to_pixel([[0, 0], [length_m, 0], [0, length_m]])
    cv2.arrowedLine(img, pt(o), pt(px), RED, 3, cv2.LINE_AA, tipLength=0.15)
    cv2.arrowedLine(img, pt(o), pt(py), GREEN, 3, cv2.LINE_AA, tipLength=0.15)
    cv2.putText(img, "+x", pt(px + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, RED, 2)
    cv2.putText(img, "+y", pt(py + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, GREEN, 2)


def draw_grid(img, fm, layout=None, step=0.5):
    """0.5 m floor grid around the layout's markers, plus the +x / +y axes at the origin."""
    if layout is not None:
        xs = [p[0] for p in layout.markers.values()]
        ys = [p[1] for p in layout.markers.values()]
        x0, x1 = math.floor(min(xs) / step) * step - step, math.ceil(max(xs) / step) * step + step
        y0, y1 = math.floor(min(ys) / step) * step - step, math.ceil(max(ys) / step) * step + step
        for x in np.arange(x0, x1 + 1e-9, step):
            a, b = fm.to_pixel([[x, y0], [x, y1]])
            cv2.line(img, pt(a), pt(b), GREY, 1, cv2.LINE_AA)
        for y in np.arange(y0, y1 + 1e-9, step):
            a, b = fm.to_pixel([[x0, y], [x1, y]])
            cv2.line(img, pt(a), pt(b), GREY, 1, cv2.LINE_AA)
    draw_axes(img, fm)


def draw_head_arrow(img, fm, floor_xy, yaw_deg, color=YELLOW, length_m=0.5):
    tip = fm.to_pixel([[floor_xy[0] + length_m * math.cos(math.radians(yaw_deg)),
                        floor_xy[1] + length_m * math.sin(math.radians(yaw_deg))]])[0]
    start = fm.to_pixel([floor_xy])[0]
    cv2.arrowedLine(img, pt(start), pt(tip), color, 2, cv2.LINE_AA, tipLength=0.2)
