"""SightLine data collection: stream a Muse S over BrainFlow, track head
orientation (relative heading / pitch / roll) live, and record everything.

An alternative to muse_collection.py that talks to the headband directly through
BrainFlow (no separate muselsl process) and fuses gyro + accel into heading, pitch
and roll. Follows the repo conventions: heading in degrees, CCW positive seen from
above, [-180, 180); timestamps (`t` columns) on pylsl.local_clock().

Usage examples
  python brainflow_collection.py                                  # Muse S, built-in Bluetooth
  python brainflow_collection.py --serial-number MuseS-1A2B       # pick a specific headband
  python brainflow_collection.py --board muse-s-bled --serial-port COM3   # BLED112 dongle
  python brainflow_collection.py --board synthetic --duration 10  # dry run, no hardware

Live keys (Windows: single keypress; elsewhere: type + Enter)
  z      zero heading: "the direction I'm facing now is 0" (face floor +x first)
  1-9    ground-truth marker: "I'm now facing target N"
  m      generic marker
  q      quit (Ctrl+C also works)
"""

from __future__ import annotations

import argparse
import platform
import queue
import sys
import threading
import time

import numpy as np
from brainflow.board_shim import BoardShim

from sightline import clock
from sightline.device import BOARDS, MuseDevice
from sightline.orientation import HeadOrientationTracker, calibrate_still
from sightline.recorder import SessionRecorder


# --------------------------------------------------------------------------- keyboard
def start_key_reader(q: "queue.Queue[str]") -> None:
    if platform.system() == "Windows":
        import msvcrt

        def loop():
            while True:
                if msvcrt.kbhit():
                    q.put(msvcrt.getwch().lower())
                time.sleep(0.02)
    else:
        def loop():
            for line in sys.stdin:
                if line.strip():
                    q.put(line.strip().lower()[0])
    threading.Thread(target=loop, daemon=True).start()


# --------------------------------------------------------------------------- calibration
def run_calibration(dev: MuseDevice, seconds: float, attempts: int, strict: bool):
    lay = dev.imu
    for i in range(1, attempts + 1):
        print(f"[calib] Keep your head STILL and face forward for {seconds:.0f} s "
              f"(attempt {i}/{attempts})...")
        dev.pull_imu()  # discard anything buffered
        time.sleep(seconds)
        rows = dev.pull_imu()
        if rows.shape[1] < 10:
            print(f"[calib] only {rows.shape[1]} IMU samples arrived - is the headband streaming?")
            continue
        cal = calibrate_still(rows[lay.accel].T, rows[lay.gyro].T)
        print(f"[calib] n={cal.n_samples} gyro bias={np.round(cal.gyro_bias_dps, 3)} deg/s "
              f"gyro noise={cal.gyro_std_dps:.2f} deg/s accel-norm noise={cal.accel_norm_std_g:.3f} g "
              f"up(sensor)={np.round(cal.up_body, 3)}")
        if cal.is_still or not strict:
            return cal, rows
        print("[calib] movement detected, retrying.")
    raise SystemExit("Calibration failed: could not get a still period. "
                     "Check the headband fit and try again (or pass --no-strict-calib).")


# --------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", choices=BOARDS, default="muse-s")
    ap.add_argument("--serial-number", default="", help="Muse name, e.g. MuseS-1A2B (default: first found)")
    ap.add_argument("--serial-port", default="", help="BLED112 COM port (muse-s-bled only)")
    ap.add_argument("--mac-address", default="")
    ap.add_argument("--timeout", type=int, default=15, help="BLE discovery timeout (s)")
    ap.add_argument("--subject", default="", help="label appended to the session folder")
    ap.add_argument("--out", default="data", help="root folder for sessions")
    ap.add_argument("--duration", type=float, default=0, help="stop after N s (0 = until q)")
    ap.add_argument("--calib-seconds", type=float, default=5.0)
    ap.add_argument("--no-strict-calib", action="store_true", help="accept calibration even if motion seen")
    ap.add_argument("--forward-axis", default="+x", choices=["+x", "-x", "+y", "-y", "+z", "-z"],
                    help="sensor axis pointing out of the face (affects pitch/roll only)")
    ap.add_argument("--invert-heading", action="store_true", help="flip heading sign if turning LEFT reads negative")
    ap.add_argument("--auto-bias", action="store_true",
                    help="re-estimate gyro bias whenever the head is still >1 s (less drift; "
                         "may absorb very slow turns)")
    ap.add_argument("--beta", type=float, default=0.04, help="Madgwick gain (higher = trust accel more)")
    ap.add_argument("--no-eeg", action="store_true", help="don't record EEG")
    ap.add_argument("--debug", action="store_true", help="enable BrainFlow debug logging")
    args = ap.parse_args()

    if args.debug:
        BoardShim.enable_dev_board_logger()
    else:
        BoardShim.disable_board_logger()

    dev = MuseDevice(args.board, args.serial_number, args.serial_port, args.mac_address, args.timeout)
    print(f"[device] connecting to {args.board} (IMU {dev.imu.rate_hz:.0f} Hz, EEG {dev.eeg.rate_hz:.0f} Hz)...")
    dev.start()
    print("[device] streaming.")

    rec = None
    try:
        strict = not args.no_strict_calib and args.board != "synthetic"
        cal, _ = run_calibration(dev, args.calib_seconds, attempts=3, strict=strict)
        tracker = HeadOrientationTracker(cal, dev.imu.rate_hz, args.forward_axis, args.beta,
                                         args.invert_heading, args.auto_bias)

        rec = SessionRecorder(args.out, args.subject, None if args.no_eeg else dev.eeg.names)
        rec.write_meta({
            "board": args.board, "board_id": dev.board_id, "serial_number": args.serial_number,
            "imu_rate_hz": dev.imu.rate_hz, "eeg_rate_hz": dev.eeg.rate_hz, "eeg_names": dev.eeg.names,
            "calibration": cal.__dict__, "forward_axis": args.forward_axis,
            "invert_heading": args.invert_heading, "auto_bias": args.auto_bias, "beta": args.beta,
            "started_unix": time.time(), "clock": clock.CLOCK_NAME,
            "notes": "heading is relative to tare (no magnetometer); deg, CCW + from above. "
                     "t columns = pylsl.local_clock() s; timestamp_unix = BrainFlow time. "
                     "pitch + = up, roll + = right ear down.",
        })
        rec.event("calibrated")
        rec.event("tare")
        dev.insert_marker(1)
        print(f"[rec] writing to {rec.dir}")
        print("[keys] z=zero heading  1-9=target marker  m=marker  q=quit\n")

        keys: "queue.Queue[str]" = queue.Queue()
        start_key_reader(keys)
        lay = dev.imu
        t0 = time.time()
        last_print = 0.0
        last_flush = time.time()
        sample = None

        while True:
            # ---- keyboard
            while not keys.empty():
                k = keys.get()
                if k == "q":
                    raise KeyboardInterrupt
                if k == "z":
                    tracker.tare()
                    rec.event("tare")
                    dev.insert_marker(1)
                    print("\n[tare] heading zeroed")
                elif k.isdigit() and k != "0":
                    rec.event("target", f"target_{k}", 10 + int(k))
                    dev.insert_marker(10 + int(k))
                    print(f"\n[marker] facing target {k}")
                elif k == "m":
                    rec.event("marker", "", 2)
                    dev.insert_marker(2)
                    print("\n[marker]")

            # ---- IMU -> orientation
            rows = dev.pull_imu()
            for i in range(rows.shape[1]):
                col = rows[:, i]
                a, g = col[lay.accel], col[lay.gyro]
                sample = tracker.update(a, g)
                ts = col[lay.timestamp]
                rec.imu.writerow([f"{clock.from_unix(ts):.6f}", f"{ts:.6f}", int(col[lay.package]),
                                  *np.round(a, 5), *np.round(g, 4),
                                  round(sample.heading_deg, 3), round(sample.pitch_deg, 3),
                                  round(sample.roll_deg, 3), int(sample.still),
                                  *np.round(sample.quat, 6)])

            # ---- EEG
            if not args.no_eeg:
                eeg = dev.pull_eeg()
                el = dev.eeg
                for i in range(eeg.shape[1]):
                    ts = eeg[el.timestamp, i]
                    rec.eeg.writerow([f"{clock.from_unix(ts):.6f}", f"{ts:.6f}",
                                      *np.round(eeg[el.channels, i], 3)])

            now = time.time()
            if sample is not None and now - last_print > 0.2:
                print(f"\rheading {sample.heading_deg:+7.1f}°  pitch {sample.pitch_deg:+6.1f}°  "
                      f"roll {sample.roll_deg:+6.1f}°  {'still ' if sample.still else 'moving'}  "
                      f"t={now - t0:6.1f}s", end="", flush=True)
                last_print = now
            if now - last_flush > 2.0:
                rec.flush()
                last_flush = now
            if args.duration and now - t0 >= args.duration:
                break
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        print()
        if rec:
            rec.event("stop")
            rec.close()
            print(f"[rec] saved session to {rec.dir}")
        dev.stop()
        print("[device] disconnected.")


if __name__ == "__main__":
    main()
