"""Tracking tests (guide page 9) on a tracks CSV from track.py. Times come from the clip log.

Usage (run from vision/):
  python scripts/check_tracks.py parked  results/tracks_A.csv --start 0 --end 10
  python scripts/check_tracks.py speed   results/tracks_A.csv --start 12.3 --end 16.8 [--dist 2.0]
  python scripts/check_tracks.py ids     results/tracks_E.csv
  python scripts/check_tracks.py heading results/tracks_D.csv --start 3 --end 9 --target 2.0 1.0

parked:  truck still -> speed < 0.05 m/s and wobble < 2 cm
speed:   --start/--end are when the truck crosses lines L1 and L2 (--dist apart, default 2 m);
         the tracked speed must be within 10% of dist / (end - start)
ids:     clip E -> the machine keeps one track ID the whole clip
heading: one clip D run -> while moving, the velocity points at the worker spot (--target x y) within 15°
Each test prints PASS or FAIL and exits 1 on FAIL.
"""
import argparse, csv, math, sys
import numpy as np


def machine_rows(path, start=None, end=None):
    rows = []
    for r in csv.DictReader(open(path)):
        if r["cls"] != "machine":
            continue
        t = float(r["t"])
        if (start is not None and t < start) or (end is not None and t > end):
            continue
        rows.append((t, int(r["id"]), float(r["x_m"]), float(r["y_m"]), float(r["vx"]), float(r["vy"])))
    if not rows:
        raise SystemExit("no machine rows in that time range")
    return np.array(rows)


def verdict(ok, msg):
    print(("PASS  " if ok else "FAIL  ") + msg)
    return ok


def parked(a):
    r = machine_rows(a.csv, a.start, a.end)
    xy = r[:, 2:4]
    wobble_cm = np.linalg.norm(xy - np.median(xy, axis=0), axis=1).max() * 100
    v = np.hypot(r[:, 4], r[:, 5])
    speed = np.nanmedian(v)
    print(f"{len(r)} frames, position {np.median(xy, axis=0).round(3)} m")
    return all([verdict(speed < 0.05, f"median speed {speed:.3f} m/s (target < 0.05)"),
                verdict(wobble_cm < 2, f"wobble {wobble_cm:.1f} cm (furthest from the median; target < 2)")])


def speed(a):
    true = a.dist / (a.end - a.start)
    r = machine_rows(a.csv, a.start, a.end)
    v = np.hypot(r[:, 4], r[:, 5])
    tracked = np.nanmedian(v)
    # cross-check that doesn't use the velocity fit: distance between first and last position / time
    path = np.linalg.norm(r[-1, 2:4] - r[0, 2:4]) / max(r[-1, 0] - r[0, 0], 1e-9)
    err = abs(tracked - true) / true
    print(f"true {true:.3f} m/s from {a.dist} m in {a.end - a.start:.2f} s; end-to-end positions give {path:.3f} m/s")
    return verdict(err <= 0.10, f"tracked median speed {tracked:.3f} m/s, off by {err:.0%} (target within 10%)")


def ids(a):
    r = machine_rows(a.csv)
    ids_ = r[:, 1].astype(int)
    switches = int((np.diff(ids_) != 0).sum())
    print(f"{len(r)} machine frames over {r[-1, 0] - r[0, 0]:.1f} s, IDs seen: {sorted(set(ids_.tolist()))}")
    return verdict(len(set(ids_.tolist())) == 1, f"{len(set(ids_.tolist()))} machine ID(s), {switches} switch(es) (target: 1 ID)")


def heading(a):
    r = machine_rows(a.csv, a.start, a.end)
    v = r[:, 4:6]
    moving = np.hypot(v[:, 0], v[:, 1]) > a.min_speed
    if not moving.any():
        raise SystemExit(f"the truck never moves faster than {a.min_speed} m/s in that window")
    to_target = np.array(a.target) - r[moving, 2:4]
    ang = np.degrees(np.arctan2(v[moving, 1], v[moving, 0]) - np.arctan2(to_target[:, 1], to_target[:, 0]))
    ang = np.abs((ang + 180) % 360 - 180)
    med = float(np.median(ang))
    print(f"{int(moving.sum())} moving frames; angle to the worker: median {med:.1f}°, worst {ang.max():.1f}°")
    return verdict(med <= 15, f"median heading error {med:.1f}° (target within 15°)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("test", choices=["parked", "speed", "ids", "heading"])
    p.add_argument("csv")
    p.add_argument("--start", type=float, help="seconds into the clip")
    p.add_argument("--end", type=float, help="seconds into the clip")
    p.add_argument("--dist", type=float, default=2.0, help="speed: metres between L1 and L2")
    p.add_argument("--target", type=float, nargs=2, metavar=("X", "Y"), help="heading: worker spot in metres")
    p.add_argument("--min-speed", type=float, default=0.1, help="heading: ignore frames slower than this (m/s)")
    a = p.parse_args()
    if a.test in ("parked", "speed", "heading") and (a.start is None or a.end is None):
        p.error(f"{a.test} needs --start and --end")
    if a.test == "heading" and a.target is None:
        p.error("heading needs --target X Y")
    ok = {"parked": parked, "speed": speed, "ids": ids, "heading": heading}[a.test](a)
    sys.exit(0 if ok else 1)
