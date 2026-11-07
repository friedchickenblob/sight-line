"""Fill in the guide's tables from recordings. One subcommand per experiment.

    python scripts/analyze.py axis        data/raw/axis.csv                 # page 4a
    python scripts/analyze.py tilt        data/raw/tilt_down.csv            # page 4c (one row)
    python scripts/analyze.py drift       data/raw/d1_desk.csv              # page 5c D1, D2, D4
    python scripts/analyze.py drift-marks data/raw/d3_active.csv            # page 5c D3, D5
    python scripts/analyze.py scale       data/raw/tape_max.csv             # page 6a
    python scripts/analyze.py fast        data/raw/fast.csv                 # page 6b
    python scripts/analyze.py glances     data/raw/glance_max.csv --target 60   # page 6c
    python scripts/analyze.py lag         data/raw/joint.csv                # page 9b (needs CAM rows)
    python scripts/analyze.py blend       data/raw/joint.csv                # page 9d
    python scripts/analyze.py bluetooth   data/raw/b1_3m_toward.csv         # page 7b

Add --plot out/<name>.png for a picture, --save to write measured values into
calibration.json (axis: axis, signs and nose axis; scale; drift: the auto re-bias
threshold, from the D2 worn-still recording), and --json for machine-readable output.
Every recording must start with 5 s standing still.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from headmotion import analysis as A              # noqa: E402
from headmotion.calibration import Calibration    # noqa: E402
from headmotion.sources import GYRO, read_csv     # noqa: E402


def _plot(path, title, series, marks=None, hlines=()):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 4))
    for label, (t, y) in series.items():
        ax.plot(t, y, lw=0.9, label=label)
    for m in marks if marks is not None else []:
        ax.axvline(m, color="0.6", lw=0.6, ls=":")
    for h in hlines:
        ax.axhline(h, color="0.4", lw=0.6, ls="--")
    ax.set_title(title); ax.set_xlabel("s"); ax.grid(alpha=0.3); ax.legend(loc="upper right")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["axis", "tilt", "drift", "drift-marks", "scale", "fast", "glances",
                                     "lag", "blend", "bluetooth"])
    ap.add_argument("csv")
    ap.add_argument("--target", type=float, default=60.0, help="glance target bearing, deg")
    ap.add_argument("--expected", type=float, default=0.0, help="drift-marks: bearing faced at each mark")
    ap.add_argument("--plot")
    ap.add_argument("--save", action="store_true", help="write measured values into calibration.json")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    s = read_csv(args.csv)
    cal = Calibration.load()
    t0 = next(x.t for x in s if x.kind == GYRO)
    rel = lambda t: np.asarray(t) - t0
    marks = rel(A.mark_times(s))

    if args.what == "axis":
        r = A.axis_experiment(s)
        if args.save:
            cal.yaw_axis, cal.axis_sign, cal.sign, cal.gyro_sign = r["yaw_axis"], r["axis_sign"], r["sign"], r["gyro_sign"]
            cal.nose_axis = r["nose_axis"]
            for k in ("yaw_axis", "axis_sign", "sign", "gyro_sign", "nose_axis"):
                cal.status[k] = f"measured from {Path(args.csv).name}"
            cal.save(); r["saved"] = True
        if args.plot:
            tg, g = A.arrays(s, GYRO)
            r["plot"] = _plot(args.plot, "axis experiment", {n: (rel(tg), g[:, k]) for k, n in enumerate(("gx", "gy", "gz"))})
    elif args.what == "tilt":
        r = A.tilt_comparison(s, cal)
    elif args.what == "drift":
        r = A.drift_still(s, cal)
        r_off = A.drift_still(s, cal, auto_rebias=False)
        r["without_auto_rebias_deg_per_min"] = r_off["drift_deg_per_min"]
        if args.save:
            cal.rebias_max_std_dps = r["suggested_rebias_max_std_dps"]
            cal.status["rebias_max_std_dps"] = f"measured from {Path(args.csv).name}"
            cal.save(); r["saved"] = True
        if args.plot:
            t, y = A.gyro_yaw(s, cal)
            r["plot"] = _plot(args.plot, f"drift: {r['drift_deg_per_min']:.2f} deg/min", {"gyro yaw": (rel(t), y)})
    elif args.what == "drift-marks":
        r = A.drift_at_marks(s, args.expected, cal)
    elif args.what == "scale":
        r = A.scale_factor(s, cal=Calibration(**{**cal.__dict__, "scale": 1.0}))
        if args.save and not r["warning"]:
            cal.scale = r["scale"]; cal.status["scale"] = f"measured from {Path(args.csv).name}"
            cal.save(); r["saved"] = True
    elif args.what == "fast":
        r = A.fast_turns(s, cal.gyro_limit_dps, cal=cal)
        if args.plot:
            tg, g = A.arrays(s, GYRO)
            r["plot"] = _plot(args.plot, "fast turns: flat tops = clipping",
                              {n: (rel(tg), g[:, k]) for k, n in enumerate(("gx", "gy", "gz"))},
                              marks, (cal.gyro_limit_dps, -cal.gyro_limit_dps))
    elif args.what == "glances":
        t, y = A.gyro_yaw(s, cal)
        d = A.glance_durations(t, y, args.target)
        r = dict(n=len(d), durations_s=np.round(d, 3).tolist(),
                 median_s=float(np.median(d)) if len(d) else None,
                 share_under_0_3s=float(np.mean(d < 0.3)) if len(d) else None)
    elif args.what == "lag":
        r = A.estimate_lag(s, cal)
    elif args.what == "blend":
        r = A.blend_sweep(s, cal=cal)
    elif args.what == "bluetooth":
        r = A.bluetooth(s)

    if args.json:
        print(json.dumps(r, indent=2, default=float))
    elif isinstance(r, list):
        keys = list(r[0])
        print("  ".join(f"{k:>22s}" for k in keys))
        for row in r:
            print("  ".join(f"{row[k]:>22.3f}" for k in keys))
    else:
        for k, v in r.items():
            print(f"{k:32s} {v}")


if __name__ == "__main__":
    main()
