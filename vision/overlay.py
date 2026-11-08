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


def draw_floor_markers(img, found, ids, color=(255, 255, 0)):
    """Outline the floor markers that were seen this frame, with their ids."""
    for mid in ids:
        if mid in found:
            c = np.asarray(found[mid], dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(img, [c], True, color, 2, cv2.LINE_AA)
            label(img, f"floor {mid}", found[mid].min(axis=0) - (0, 6), color, 0.45)


def _dial_pt(c, r, deg):
    """Top-down dial: 0 deg = +x to the right, CCW positive (screen y points down)."""
    a = math.radians(deg)
    return int(round(c[0] + r * math.cos(a))), int(round(c[1] - r * math.sin(a)))


def draw_angle_panel(img, head_yaw, bearing, rel, facing_deg, target="toy car", heading_note=""):
    """Top-right panel: a top-down dial with the worker's heading (yellow), the direction to the
    target (orange) and the +-facing_deg cone, plus the numbers and a FACING / NOT FACING verdict.
    head_yaw is None while the live heading isn't ready; bearing / rel are None when the target,
    the floor map or the heading is missing. heading_note says where the heading comes from."""
    h, w = img.shape[:2]
    s = max(0.7, min(1.5, h / 720))
    pw, ph = int(250 * s), int(365 * s)
    x0, y0 = w - pw - 10, 40
    if x0 < 0:
        return
    roi = img[y0:y0 + ph, x0:x0 + pw]
    roi[:] = (roi * 0.25 + 15).astype(img.dtype)          # darken the panel background
    r = int(85 * s)
    c = (x0 + pw // 2, y0 + int(35 * s) + r)
    facing = rel is not None and abs(rel) <= facing_deg
    verdict_col = GREEN if facing else RED if rel is not None else GREY

    # facing cone around the heading
    if head_yaw is not None:
        cone = [c] + [_dial_pt(c, r, head_yaw + a) for a in np.linspace(-facing_deg, facing_deg, 24)]
        overlay = img.copy()
        cv2.fillPoly(overlay, [np.array(cone, np.int32)], verdict_col)
        cv2.addWeighted(overlay, 0.25, img, 0.75, 0, img)
    cv2.circle(img, c, r, GREY, 1, cv2.LINE_AA)
    for a, t in ((0, "+x"), (90, "+y")):
        cv2.putText(img, t, _dial_pt(c, r + 14 * s, a), cv2.FONT_HERSHEY_SIMPLEX, 0.4 * s, GREY, 1, cv2.LINE_AA)
    if bearing is not None:
        cv2.arrowedLine(img, c, _dial_pt(c, r, bearing), ORANGE, 2, cv2.LINE_AA, tipLength=0.15)
    if head_yaw is not None:
        cv2.arrowedLine(img, c, _dial_pt(c, r, head_yaw), YELLOW, 3, cv2.LINE_AA, tipLength=0.18)
    cv2.circle(img, c, 4, WHITE, -1)

    side = "" if rel is None else " ahead" if abs(rel) < 1 else " left" if rel > 0 else " right"
    lines = [("worker heading " + ("   --" if head_yaw is None else f"{head_yaw:+5.0f} deg"), YELLOW),
             (f"{target} bearing " + ("   --" if bearing is None else f"{bearing:+5.0f} deg"), ORANGE),
             ("relative angle " + ("   --" if rel is None else f"{rel:+5.0f} deg{side}"), WHITE)]
    y = c[1] + r + int(30 * s)
    for t, col in lines:
        cv2.putText(img, t, (x0 + 10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * s, col, 1, cv2.LINE_AA)
        y += int(22 * s)
    verdict = ("heading not ready" if head_yaw is None else f"{target} not seen" if rel is None else
               f"FACING (|rel| <= {facing_deg:.0f})" if facing else "NOT FACING")
    cv2.putText(img, verdict, (x0 + 10, y + int(4 * s)), cv2.FONT_HERSHEY_SIMPLEX, 0.65 * s, verdict_col, 2, cv2.LINE_AA)
    if heading_note:
        cv2.putText(img, f"heading: {heading_note}", (x0 + 10, y + int(26 * s)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.42 * s, GREY, 1, cv2.LINE_AA)
