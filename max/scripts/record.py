"""Record a session to CSV (page 3, page 7c). Press Enter to drop a MARK.

    python scripts/record.py --out data/raw/axis_01.csv --seconds 60
    python scripts/record.py --source synthetic:axis --out data/fake/axis.csv

Every good session gets recorded from now on: it is the replay safety net.
At the end it prints the page-3 checks (rate, drops per minute, biggest gap).
"""
import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from headmotion.cli import add_source_args, camera_subscriber, muse_kwargs  # noqa: E402
from headmotion.health import gap_stats                     # noqa: E402
from headmotion.sources import DROP, GYRO, MARK, CsvRecorder, Sample, open_source  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_source_args(ap)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=None, help="stop after this long (default: Ctrl+C)")
    args = ap.parse_args()

    live = args.source == "muse"
    cam = camera_subscriber(args)                 # --camera-topic: also record Hanh's camera yaw
    src = open_source(args.source, realtime=False, **(muse_kwargs(args, [cam] if cam else []) if live else {}))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    marks = []
    if live:
        def listen():
            for _ in sys.stdin:
                marks.append(src.now())
                print(f"  MARK {len(marks) - 1} at {marks[-1]:.2f}")
        threading.Thread(target=listen, daemon=True).start()
        print("Recording. Stand still for the first 5 s (bias). Press Enter to MARK. Ctrl+C to stop.")

    gyro_t, drop_t, drop_n, t_start, n_marks = [], [], [], None, 0
    with CsvRecorder(args.out) as rec:
        try:
            for batch in rec.tee(src.batches()):
                while marks:
                    rec.write(Sample(MARK, marks.pop(0), (float(n_marks),)))
                    n_marks += 1
                for s in batch:
                    if s.kind == GYRO:
                        gyro_t.append(s.t)
                        t_start = t_start if t_start is not None else s.t
                    elif s.kind == DROP:
                        drop_t.append(s.t); drop_n.append(s.v[0])
                while live and src.events:
                    print(f"\n  {src.events.popleft()}")
                if args.seconds and t_start is not None and gyro_t and gyro_t[-1] - t_start >= args.seconds:
                    break
                if live and gyro_t and src.now() - gyro_t[-1] > 0.5:
                    print(f"\r  WARNING: no gyro for {src.now() - gyro_t[-1]:.1f} s", end="", flush=True)
        except KeyboardInterrupt:
            pass
        finally:
            for x in (cam, src if live else None):
                if x is not None:
                    x.close()
    st = gap_stats(gyro_t, drop_t, drop_n)
    print(f"\nwrote {rec.count} samples to {args.out}")
    print(f"gyro: {st['n']} samples, {st['rate_hz']:.1f} Hz, {st['drops_per_min']:.1f} drops/min "
          f"({st['lost_samples']} samples lost), biggest gap {st['biggest_gap_ms']:.0f} ms, {st['disconnects']} disconnects (> 2 s)")


if __name__ == "__main__":
    main()
