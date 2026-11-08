"""Live head yaw on screen (the Sun Nov 1 check-in), with the Bluetooth watchdog.

    python scripts/live_yaw.py                          # Muse S over BrainFlow
    python scripts/live_yaw.py --mqtt localhost         # ...and publish head_yaw + muse_health
    python scripts/live_yaw.py --source replay:data/raw/session.csv
    python scripts/live_yaw.py --source synthetic:joint
    python scripts/live_yaw.py --reference-bearing 90   # also snap to a wall sign at 90 deg (page 6d)

Stand still for the first 5 s (bias), facing the start line (+x). Then press
Enter at any time to re-zero yaw to 0 (start-of-run reset, page 6d). With
--record, re-zeros are saved too, so a replay behaves exactly like the live run.
"""
import argparse
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from headmotion.calibration import Calibration      # noqa: E402
from headmotion.cli import add_source_args, camera_subscriber, muse_kwargs  # noqa: E402
from headmotion.health import StreamHealth          # noqa: E402
from headmotion.sources import RESET, CsvRecorder, Sample, open_source  # noqa: E402
from headmotion.yaw import HeadYawEstimator, ReferenceGlance, YawPipeline  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_source_args(ap)
    ap.add_argument("--record", help="also save everything (including re-zeros) to this CSV")
    ap.add_argument("--bias-seconds", type=float, default=5.0)
    g = ap.add_argument_group("reference-glance reset (page 6d)")
    g.add_argument("--reference-bearing", type=float, help="bearing of the wall sign, deg; turns the reset on")
    g.add_argument("--reference-tol", type=float, default=10.0, help="snap when within this many deg of the sign")
    g.add_argument("--reference-hold", type=float, default=1.5, help="...and still for this many seconds")
    args = ap.parse_args()

    cal = Calibration.load()
    if cal.pending():
        print("note: calibration still has placeholders for:", ", ".join(cal.pending()))
    live = args.source == "muse"
    cam = camera_subscriber(args)
    src = open_source(args.source, realtime=not live, **(muse_kwargs(args, [cam] if cam else []) if live else {}))
    pub = None
    if args.mqtt:
        from headmotion.bus import Publisher, parse_host
        pub = Publisher(*parse_host(args.mqtt))
    reference = None
    if args.reference_bearing is not None:
        reference = ReferenceGlance(args.reference_bearing, args.reference_tol, args.reference_hold)
    est = HeadYawEstimator(cal, reference=reference)
    pipe = YawPipeline(est, args.bias_seconds, start_yaw=0.0, retry_bias=True)
    health = StreamHealth()
    enter = []
    if live or sys.stdin.isatty():
        threading.Thread(target=lambda: [enter.append(1) for _ in sys.stdin], daemon=True).start()

    rec = CsvRecorder(args.record).__enter__() if args.record else None
    batches = rec.tee(src.batches()) if rec else src.batches()
    print(f"Hold still for {args.bias_seconds:.0f} s, facing the start line...")
    msg, shown, next_pub = None, 0, float("-inf")
    try:
        for batch in batches:
            if enter and pipe.running:                 # operator re-zero: goes through the stream
                enter.clear()
                reset = Sample(RESET, src.now(), (0.0,))
                if rec:
                    rec.write(reset)
                batch = batch + [reset]
            for s in batch:
                health.feed(s)
                out = pipe.feed(s)
                if out is not None:
                    msg = out
                    if pub:
                        pub.publish(out)
            for e in pipe.events[shown:]:
                print(f"\n-- {e}" + (". Go. (Enter = re-zero)" if e.startswith("bias") else ""))
            shown = len(pipe.events)
            while live and src.events:
                print(f"\n-- {src.events.popleft()}")
            now, battery = src.now(), getattr(src, "battery_pct", None)
            if pub and now >= next_pub:                # muse_health: 1 per second (page 10)
                next_pub = now + 1.0
                pub.publish(health.report(now, battery))
            h = health.maybe_report(now, 0.25, battery)
            if h is not None:
                warn = "  ** NO GYRO DATA **" if not h.connected else ""
                if msg is not None and msg.yaw_deg is not None:
                    bar = int((msg.yaw_deg + 180) / 360 * 40)
                    line = (f"yaw {msg.yaw_deg:+7.1f}  rate {msg.rate_dps:+7.1f}  [{'.' * bar}|{'.' * (40 - bar)}]  "
                            f"{msg.source:5s} {'ok ' if msg.valid else 'BAD'}  {h.rate_hz:4.0f} Hz  "
                            f"drops {h.drops_per_min:.0f}/min{warn}")
                else:
                    line = f"waiting...  {h.rate_hz:4.0f} Hz{warn}"
                print("\r" + line.ljust(118), end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        if rec:
            rec.__exit__(None, None, None)
            print(f"\nrecorded {rec.count} samples to {args.record}")
        for x in (pub, cam, src if live else None):
            if x is not None:
                x.close()
    print(f"\nclipped samples: {est.clipped_samples}, gaps skipped: {est.gaps}, rebias: {est.rebias_count}, "
          f"camera frames rejected: {est.camera_rejected}, packets lost: {health.total_drops} "
          f"({health.lost_samples} samples)" + (f", published: {pub.sent}" if pub else ""))


if __name__ == "__main__":
    main()
