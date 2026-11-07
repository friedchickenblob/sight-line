"""Page 8 simulator: run the five synthetic scenes and plot the flag.

    python scripts/simulate_contact.py --plot out/contact_scenes.png
    python scripts/simulate_contact.py --R 0.5 --T 2.0 --seeds 20     # tune R and T
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from headmotion.contact_sim import all_scenes, run_scene, transitions  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--R", type=float, default=0.5)
    ap.add_argument("--T", type=float, default=2.0)
    ap.add_argument("--seeds", type=int, default=10, help="noise seeds per scene")
    ap.add_argument("--plot")
    args = ap.parse_args()

    scenes = all_scenes()
    print(f"R = {args.R} m, T = {args.T} s, {args.seeds} noise seeds each\n")
    print(f"{'scene':52s} {'expected':38s} {'fired':>6s} {'flicker raw':>12s} {'flicker hyst':>13s}")
    for sc in scenes:
        fired = raw_f = hyst_f = 0
        for k in range(args.seeds):
            r = run_scene(sc, args.R, args.T, seed=k)
            fired += r["flag"].any()
            hyst_f += transitions(r["flag"])
            raw_f += transitions(run_scene(sc, args.R, args.T, seed=k, hysteresis=False)["flag"])
        print(f"{sc.name:52s} {sc.expect:38s} {fired:3d}/{args.seeds:<2d} {raw_f / args.seeds:12.1f} {hyst_f / args.seeds:13.1f}")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(len(scenes), 1, figsize=(10, 2.1 * len(scenes)), sharex=True)
        for ax, sc in zip(axes, scenes):
            r = run_scene(sc, args.R, args.T, seed=0)
            raw = run_scene(sc, args.R, args.T, seed=0, hysteresis=False)
            ax.plot(r["t"], r["d"], color="#444", lw=1.2, label="distance (m)")
            ax.plot(r["t"], np.clip(r["ttc"], 0, 5), color="#2a6fdb", lw=1.0, label="ttc (s, clipped at 5)")
            ax.axhline(args.T, color="#2a6fdb", ls=":", lw=0.8)
            ax.fill_between(r["t"], 0, 4.4, where=r["flag"], color="#d1495b", alpha=0.25, step="mid", label="flag (hysteresis)")
            ax.fill_between(r["t"], 4.6, 5, where=raw["flag"], color="#e07b00", alpha=0.9, step="mid", label="raw flag (no hysteresis)")
            ax.set_ylim(0, 5); ax.set_title(f"{sc.name}  -  should: {sc.expect}", fontsize=9, loc="left")
            ax.grid(alpha=0.3)
        axes[0].legend(loc="upper right", fontsize=7, ncol=4)
        axes[-1].set_xlabel("s")
        fig.tight_layout()
        Path(args.plot).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(args.plot, dpi=110)
        print(f"\nplot: {args.plot}")


if __name__ == "__main__":
    main()
