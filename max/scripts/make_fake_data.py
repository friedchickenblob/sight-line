"""Write a synthetic recording for every experiment into data/fake/, so every
analysis command can be tried before the Muse arrives.

    python scripts/make_fake_data.py
    python scripts/analyze.py axis data/fake/axis.csv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from headmotion import synth            # noqa: E402
from headmotion.sources import write_csv  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "fake"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name in synth.SCENE_NAMES:
        n = write_csv(OUT / f"{name}.csv", synth.scene(name))
        print(f"{name:12s} {n:7d} samples -> {OUT / (name + '.csv')}")


if __name__ == "__main__":
    main()
