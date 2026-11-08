"""Page 3 'Check before moving on' for one recording, plus a plot of all 3 axes.

    python scripts/check_stream.py data/raw/first_30s.csv --plot out/first_30s.png
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from headmotion.analysis import arrays, bluetooth  # noqa: E402
from headmotion.sources import ACC, GYRO, read_csv  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--plot", help="save a PNG of gx, gy, gz")
    args = ap.parse_args()

    s = read_csv(args.csv)
    tg, g = arrays(s, GYRO)
    ta, a = arrays(s, ACC)
    st = bluetooth(s)
    still = g[tg < tg[0] + 5]
    checks = [
        ("rate close to 52 Hz", abs(st["rate_hz"] - 52) < 3, f"{st['rate_hz']:.1f} Hz"),
        ("dropped packets per minute", st["drops_per_min"] < 1,
         f"{st['drops_per_min']:.2f} / min, {st['lost_samples']} samples lost (biggest gap {st['biggest_gap_ms']:.0f} ms)"),
        ("near 0 deg/s when still (first 5 s)", np.abs(still.mean(0)).max() < 3,
         f"mean {np.round(still.mean(0), 2)}, noise {np.round(still.std(0), 2)}"),
        ("tens-hundreds deg/s when turning", np.abs(g).max() > 30, f"peak {np.abs(g).max():.0f} deg/s"),
        ("accel magnitude ~1 g", abs(np.median(np.linalg.norm(a, axis=1)) - 1) < 0.1, f"{np.median(np.linalg.norm(a, axis=1)):.2f} g"),
    ]
    for name, ok, val in checks:
        print(f"[{'x' if ok else ' '}] {name:40s} {val}")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
        for k, n in enumerate(("gx", "gy", "gz")):
            ax[0].plot(tg - tg[0], g[:, k], lw=0.8, label=n)
            ax[1].plot(ta - tg[0], a[:, k], lw=0.8, label="axyz"[k + 1])
        ax[0].set_ylabel("gyro, deg/s"); ax[1].set_ylabel("accel, g"); ax[1].set_xlabel("s")
        for x in ax:
            x.legend(loc="upper right"); x.grid(alpha=0.3)
        fig.suptitle(Path(args.csv).name)
        Path(args.plot).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(args.plot, dpi=110, bbox_inches="tight")
        print(f"plot: {args.plot}")


if __name__ == "__main__":
    main()
