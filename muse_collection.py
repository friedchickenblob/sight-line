#!/usr/bin/env python3
"""
muse_collect.py - SightLine head-motion data collection for the Muse S (Gen 2)

Records the Muse's gyroscope and accelerometer (and optionally EEG / PPG) from the
LSL streams that muselsl publishes. Timestamps are on this laptop's
pylsl.local_clock(), so they line up with Hanh's camera frames (same laptop, same clock).

While it records, it shows the live stream health (rate, dropped packets, Bluetooth
watchdog) and a live gyro-only head yaw (bias removed, tilt-corrected). This is the
same yaw code that `replay` runs on a saved session, so a live run and a replayed
run behave the same.

SETUP (once)
    pip install muselsl pylsl numpy matplotlib

RECORD
    Terminal 1:  muselsl stream --gyro --acc        (keep it open)
    Terminal 2:  python muse_collect.py record --label axis-test --seconds 30

    Or let the script start muselsl for you:
                 python muse_collect.py record --launch --label axis-test --seconds 30

    While recording, type into the terminal and press Enter:
        <Enter>        add a marker ("mark")
        any text       add a marker with that text (e.g. "facing 90")
        r              reset yaw to 0 (start-of-run reset: face the +x start line first)
        q              stop recording

    Each session is saved to data/<date>_<time>_<label>/ :
        gyro.csv     t, gx, gy, gz       (deg/s)
        acc.csv      t, ax, ay, az       (g)
        eeg.csv / ppg.csv                (only with --eeg / --ppg)
        markers.csv  t, text
        meta.json    label, notes, stream info, bias, settings, summary

EXAMPLES (matching Max's research guide)
    Axis + sign test (p.4):   record --label axis-test --seconds 30 --plot
    Drift D1 on the desk:     record --label D1-desk --seconds 600
    Drift D3 worn, active:    record --label D3-active --seconds 120 --sign -1
    Fast turns (p.6):         record --label fast-turns --seconds 60 --plot
    Bluetooth B4 long run:    record --label B4-long --seconds 1800 --bias 0
    Joint session with Hanh:  record --label joint-hanh --seconds 180

    replay data/<session>              play it back through the yaw code, in real time
    replay data/<session> --speed 0 --save    as fast as possible, write yaw.csv
    plot   data/<session>              gyro, accel and yaw plots, saved as plot.png
    list                               show every LSL stream the laptop can see
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np

# ----------------------------------------------------------------------------- constants
GYRO_LIMIT_DPS = 245.0          # Muse gyro full scale (muselsl scale factor x 32768)
CLIP_DPS = 0.97 * GYRO_LIMIT_DPS  # a sample this large on any axis counts as clipped
GAP_S = 0.100                   # a gap longer than this between samples = dropped packet(s)
WATCHDOG_S = 0.5                # warn if no gyro data has arrived for this long
MAX_DT_S = 0.100                # never integrate across a gap: a dropped packet is not a turn
BIAS_MAX_STD = 2.0              # deg/s: more spread than this in the bias window = they moved

KINDS = ("GYRO", "ACC", "EEG", "PPG")
FIXED_COLUMNS = {"GYRO": ["gx", "gy", "gz"], "ACC": ["ax", "ay", "az"]}
FILES = {"GYRO": "gyro.csv", "ACC": "acc.csv", "EEG": "eeg.csv", "PPG": "ppg.csv"}
RESET_WORDS = {"r", "reset"}
QUIT_WORDS = {"q", "quit", "stop"}


def wrap(a: float) -> float:
    """Any angle -> [-180, 180)."""
    return (a + 180.0) % 360.0 - 180.0


# ----------------------------------------------------------------------------- yaw
class YawTracker:
    """Gyro-only head yaw on the shared floor frame (CCW from above = positive).

    The accelerometer gives the 'up' direction in the headband's own axes; the gyro
    rate is projected onto it, so the turn rate is measured about the true vertical
    whatever the head's tilt. Bias is subtracted, gaps are skipped, and the result is
    integrated using the LSL timestamps (not a fixed 1/52 s).
    """

    def __init__(self, bias=None, sign: float = 1.0, scale: float = 1.0, alpha: float = 0.02):
        self.bias = np.zeros(3) if bias is None else np.asarray(bias, float)
        self.sign, self.scale, self.alpha = float(sign), float(scale), float(alpha)
        self.up = np.array([0.0, 0.0, 1.0])
        self.have_up = False
        self.yaw = 0.0          # wrapped to [-180, 180)
        self.unwrapped = 0.0    # total turn since the last reset (for drift numbers)
        self.rate = 0.0         # latest yaw rate, deg/s
        self.last_t = None
        self.skipped_gaps = 0

    def feed(self, kind: str, t: float, v) -> None:
        if kind == "ACC":
            a = np.asarray(v, float)
            n = float(np.linalg.norm(a))
            if n < 1e-6:
                return
            a = a / n
            if not self.have_up:            # first sample replaces the initial guess
                self.up, self.have_up = a, True
            else:                           # slow average = gravity only, not head jolts
                self.up = (1.0 - self.alpha) * self.up + self.alpha * a
                self.up /= np.linalg.norm(self.up)
        elif kind == "GYRO":
            w = np.asarray(v, float) - self.bias
            self.rate = self.sign * self.scale * float(w @ self.up)
            if self.last_t is not None:
                dt = t - self.last_t
                if 0.0 < dt < MAX_DT_S:
                    self.yaw = wrap(self.yaw + self.rate * dt)
                    self.unwrapped += self.rate * dt
                elif dt >= MAX_DT_S:
                    self.skipped_gaps += 1
            self.last_t = t

    def reset(self, yaw: float = 0.0) -> None:
        self.yaw = wrap(yaw)
        self.unwrapped = 0.0


# ----------------------------------------------------------------------------- stream health
class StreamHealth:
    """Sample rate, dropped packets and clipping for one stream, from its timestamps."""

    def __init__(self, kind: str):
        self.kind = kind
        self.n = 0
        self.first_t = None
        self.last_t = None
        self.gaps = 0
        self.max_gap = 0.0
        self.dts: list[float] = []
        self.recent: deque[float] = deque()
        self.clipped = 0

    def add(self, t: float, v) -> None:
        if self.last_t is None:
            self.first_t = t
        else:
            dt = t - self.last_t
            self.dts.append(dt)
            self.max_gap = max(self.max_gap, dt)
            if dt > GAP_S:
                self.gaps += 1
        self.last_t = t
        self.n += 1
        self.recent.append(t)
        while self.recent and self.recent[0] < t - 5.0:
            self.recent.popleft()
        if self.kind == "GYRO" and max(abs(x) for x in v) >= CLIP_DPS:
            self.clipped += 1

    def rate_hz(self) -> float:
        """Rate over the last ~5 s."""
        if len(self.recent) < 2:
            return 0.0
        span = self.recent[-1] - self.recent[0]
        return (len(self.recent) - 1) / span if span > 0 else 0.0

    def minutes(self) -> float:
        if self.first_t is None or self.last_t is None:
            return 0.0
        return (self.last_t - self.first_t) / 60.0

    def drops_per_min(self) -> float:
        m = self.minutes()
        return self.gaps / m if m > 0 else 0.0

    def summary(self) -> dict:
        med = float(np.median(self.dts)) if self.dts else 0.0
        return {
            "samples": self.n,
            "duration_s": round(self.minutes() * 60.0, 3),
            "median_rate_hz": round(1.0 / med, 2) if med > 0 else 0.0,
            "biggest_gap_ms": round(self.max_gap * 1000.0, 1),
            "drops": self.gaps,
            "drops_per_min": round(self.drops_per_min(), 2),
            **({"clipped_samples": self.clipped} if self.kind == "GYRO" else {}),
        }

    def summary_line(self) -> str:
        s = self.summary()
        line = (f"{self.kind:<4}: {s['samples']} samples, {s['median_rate_hz']:.1f} Hz median, "
                f"biggest gap {s['biggest_gap_ms']:.0f} ms, {s['drops']} drops "
                f"({s['drops_per_min']:.1f}/min)")
        if self.kind == "GYRO":
            line += f", {self.clipped} clipped samples (>= {CLIP_DPS:.0f} deg/s)"
        return line


# ----------------------------------------------------------------------------- sources
# Live data and recorded files sit behind the same interface (poll() -> list of
# (t, kind, values)), so nothing downstream can tell them apart. That is the replay
# safety net from page 7: if Bluetooth dies during judging, feed a recording instead.

def _import_pylsl():
    try:
        import pylsl  # noqa: WPS433
        return pylsl
    except ImportError:
        sys.exit("pylsl is not installed. Run:  pip install muselsl pylsl numpy matplotlib")


def _channel_labels(info, kind: str) -> list[str]:
    if kind in FIXED_COLUMNS:
        return list(FIXED_COLUMNS[kind])
    n = info.channel_count()
    labels = []
    try:
        ch = info.desc().child("channels").child("channel")
        for i in range(n):
            lab = ch.child_value("label") if ch is not None else ""
            labels.append(lab or f"ch{i}")
            ch = ch.next_sibling()
    except Exception:  # some sources don't describe their channels
        labels = []
    if len(labels) != n:
        labels = [f"ch{i}" for i in range(n)]
    return labels


class LiveSource:
    """Reads the muselsl LSL streams, timestamps corrected onto this laptop's clock."""

    def __init__(self, types: dict[str, str], timeout: float = 10.0,
                 required=("GYRO", "ACC")):
        self.lsl = _import_pylsl()
        self.inlets: dict = {}
        self.info: dict[str, dict] = {}
        for kind, lsl_type in types.items():
            print(f"  looking for a {lsl_type} stream (up to {timeout:.0f} s)...", flush=True)
            found = self.lsl.resolve_byprop("type", lsl_type, timeout=timeout)
            if not found:
                if kind in required:
                    raise SystemExit(
                        f"\nNo {lsl_type} stream found. Is `muselsl stream --gyro --acc` running "
                        f"in another terminal (or use --launch)? Run `python {Path(__file__).name} "
                        f"list` to see what streams exist.")
                print(f"  no {lsl_type} stream; skipping {kind}")
                continue
            if len(found) > 1:
                names = ", ".join(f"{f.name()} [{f.source_id()}]" for f in found)
                print(f"  WARNING: {len(found)} {lsl_type} streams ({names}); using the first. "
                      f"Stop the other muselsl, or pass --address when launching.")
            inlet = self.lsl.StreamInlet(
                found[0], max_buflen=60,
                processing_flags=self.lsl.proc_clocksync | self.lsl.proc_dejitter)
            full = inlet.info(timeout=5.0)
            self.inlets[kind] = inlet
            self.info[kind] = {
                "lsl_type": lsl_type,
                "name": full.name(),
                "source_id": full.source_id(),
                "nominal_srate": full.nominal_srate(),
                "channel_count": full.channel_count(),
                "labels": _channel_labels(full, kind),
            }
            print(f"  connected: {kind} <- '{full.name()}' ({full.channel_count()} ch, "
                  f"{full.nominal_srate():.0f} Hz nominal)")
        self.last_arrival = {k: None for k in self.inlets}

    def clock(self) -> float:
        return self.lsl.local_clock()

    def poll(self) -> list:
        items = []
        now = self.clock()
        for kind, inlet in self.inlets.items():
            samples, stamps = inlet.pull_chunk(timeout=0.0)
            if stamps:
                self.last_arrival[kind] = now
                items.extend((t, kind, s) for t, s in zip(stamps, samples))
        items.sort(key=lambda x: x[0])
        return items

    def close(self) -> None:
        for inlet in self.inlets.values():
            try:
                inlet.close_stream()
            except Exception:
                pass


class FileSource:
    """Plays a saved session back as if it were live. speed=1 real time, 0 = instant."""

    def __init__(self, session_dir: Path, speed: float = 1.0, kinds=("GYRO", "ACC"),
                 include_markers: bool = True):
        self.dir = Path(session_dir)
        rows = []
        for kind in kinds:
            path = self.dir / FILES[kind]
            if not path.exists():
                continue
            data = np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)
            rows.extend((float(r[0]), kind, r[1:].tolist()) for r in data)
        if include_markers and (self.dir / "markers.csv").exists():
            for t, text in read_markers(self.dir):
                rows.append((t, "MARK", text))
        if not any(k == "GYRO" for _, k, _ in rows):
            raise SystemExit(f"No gyro.csv data in {self.dir}")
        rows.sort(key=lambda x: x[0])
        self.rows = rows
        self.i = 0
        self.speed = speed
        self.t0 = rows[0][0]
        self.wall0 = None
        self.last_arrival = {k: None for k in kinds}

    @property
    def done(self) -> bool:
        return self.i >= len(self.rows)

    def clock(self) -> float:
        """Current time on the recording's clock."""
        if self.speed <= 0:
            return self.rows[min(self.i, len(self.rows) - 1)][0]
        if self.wall0 is None:
            self.wall0 = time.perf_counter()
        return self.t0 + (time.perf_counter() - self.wall0) * self.speed

    def poll(self) -> list:
        if self.done:
            return []
        if self.speed <= 0:
            j = min(self.i + 2000, len(self.rows))
        else:
            now = self.clock()
            j = self.i
            while j < len(self.rows) and self.rows[j][0] <= now:
                j += 1
        out = self.rows[self.i:j]
        self.i = j
        for _, kind, _ in out:
            if kind in self.last_arrival:
                self.last_arrival[kind] = self.clock()
        return out


def read_markers(session_dir: Path) -> list[tuple[float, str]]:
    out = []
    path = Path(session_dir) / "markers.csv"
    if path.exists():
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                out.append((float(row["t"]), row["text"]))
    return out


# ----------------------------------------------------------------------------- saving
class SessionWriter:
    def __init__(self, root: Path, label: str, info: dict[str, dict]):
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-") or "session"
        self.dir = Path(root) / f"{stamp}_{safe}"
        self.dir.mkdir(parents=True, exist_ok=False)
        self._files, self._writers, self.rows = {}, {}, {}
        for kind, inf in info.items():
            f = open(self.dir / FILES[kind], "w", newline="")
            w = csv.writer(f)
            w.writerow(["t", *inf["labels"]])
            self._files[kind], self._writers[kind], self.rows[kind] = f, w, 0
        self._mfile = open(self.dir / "markers.csv", "w", newline="")
        self._mwriter = csv.writer(self._mfile)
        self._mwriter.writerow(["t", "text"])
        self.markers = 0

    def write(self, kind: str, t: float, v) -> None:
        w = self._writers.get(kind)
        if w is not None:
            w.writerow([f"{t:.6f}", *(f"{x:.6g}" for x in v)])
            self.rows[kind] += 1

    def marker(self, t: float, text: str) -> None:
        self._mwriter.writerow([f"{t:.6f}", text])
        self.markers += 1
        self._mfile.flush()

    def write_meta(self, meta: dict) -> None:
        with open(self.dir / "meta.json", "w") as f:
            json.dump(meta, f, indent=2)

    def flush(self) -> None:
        for f in self._files.values():
            f.flush()

    def close(self) -> None:
        for f in [*self._files.values(), self._mfile]:
            f.close()


# ----------------------------------------------------------------------------- helpers
def launch_muselsl(address: str | None, ppg: bool) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "muselsl", "stream", "--gyro", "--acc"]
    if ppg:
        cmd.append("--ppg")
    if address:
        cmd += ["--address", address]
    print("Starting:", " ".join(cmd), flush=True)
    try:
        return subprocess.Popen(cmd)
    except FileNotFoundError:
        sys.exit("Could not start muselsl. Is it installed?  pip install muselsl")


def start_marker_thread(q: queue.Queue) -> None:
    def run():
        for line in sys.stdin:
            q.put(line.strip())
    threading.Thread(target=run, daemon=True).start()


def handle_item(t, kind, v, tracker: YawTracker, health: dict, writer=None) -> None:
    """The one place every sample goes, live or replayed."""
    if kind == "MARK":
        if str(v).strip().lower() in RESET_WORDS:
            tracker.reset(0.0)
        return
    if writer is not None:
        writer.write(kind, t, v)
    h = health.get(kind)
    if h is not None:
        h.add(t, v)
    tracker.feed(kind, t, v)


def status_line(elapsed: float, health: dict, tracker: YawTracker) -> str:
    g, a = health.get("GYRO"), health.get("ACC")
    parts = [f"[{elapsed:7.1f}s]"]
    if g:
        parts.append(f"gyro {g.rate_hz():4.1f} Hz")
    if a:
        parts.append(f"acc {a.rate_hz():4.1f} Hz")
    if g:
        parts.append(f"| drops {g.gaps} ({g.drops_per_min():.1f}/min)")
    parts.append(f"| yaw {tracker.yaw:+7.1f} deg  rate {tracker.rate:+7.1f} deg/s")
    if g and g.clipped:
        parts.append(f"| clipped {g.clipped}")
    return " ".join(parts)


def measure_bias(src: LiveSource, seconds: float, tracker: YawTracker, health: dict,
                 writer: SessionWriter, attempts: int = 3) -> dict:
    """Worker stands still; average raw gyro = bias vector. Samples are still recorded."""
    for attempt in range(1, attempts + 1):
        print(f"\nBIAS: keep the head completely still for {seconds:.0f} s "
              f"(attempt {attempt}/{attempts})...", flush=True)
        rows = []
        t_start = src.clock()
        t_end = t_start + seconds
        first_t = last_t = None
        while src.clock() < t_end:
            for t, kind, v in src.poll():
                handle_item(t, kind, v, tracker, health, writer)
                if kind == "GYRO":
                    rows.append(v)
                    first_t = t if first_t is None else first_t
                    last_t = t
            time.sleep(0.01)
        if len(rows) < 0.5 * 52 * seconds:
            print(f"  only {len(rows)} gyro samples in {seconds:.0f} s: is the headband streaming?")
            continue
        arr = np.asarray(rows, float)
        std = arr.std(axis=0)
        if std.max() > BIAS_MAX_STD:
            print(f"  moved during the bias window (spread {std.max():.1f} deg/s > "
                  f"{BIAS_MAX_STD:.1f}); trying again.")
            continue
        bias = arr.mean(axis=0)
        print(f"  bias = ({bias[0]:+.3f}, {bias[1]:+.3f}, {bias[2]:+.3f}) deg/s, "
              f"spread {std.max():.2f} deg/s from {len(arr)} samples")
        writer.marker(first_t, "bias-start")
        writer.marker(last_t, "bias-end")
        return {"vector": bias.round(5).tolist(), "std": std.round(4).tolist(),
                "samples": len(arr), "window": [round(first_t, 6), round(last_t, 6)]}
    raise SystemExit("Could not get a still bias window. Sit still and run again "
                     "(or pass --bias 0 to skip it).")


# ----------------------------------------------------------------------------- commands
def cmd_record(args) -> None:
    types = {"GYRO": args.gyro_type, "ACC": args.acc_type}
    if args.eeg:
        types["EEG"] = "EEG"
    if args.ppg:
        types["PPG"] = "PPG"

    proc = launch_muselsl(args.address, args.ppg) if args.launch else None
    timeout = max(args.timeout, 45.0) if args.launch else args.timeout
    src = writer = None
    try:
        print("Connecting to the Muse LSL streams...")
        src = LiveSource(types, timeout=timeout)
        writer = SessionWriter(Path(args.out), args.label, src.info)
        print(f"Saving to {writer.dir}")

        tracker = YawTracker(sign=args.sign, scale=args.scale, alpha=args.alpha)
        health = {k: StreamHealth(k) for k in src.inlets}
        meta = {
            "label": args.label,
            "notes": args.notes,
            "started": datetime.now().isoformat(timespec="seconds"),
            "clock": "pylsl.local_clock() on this laptop (proc_clocksync | proc_dejitter)",
            "device": "Muse S (Gen 2) via muselsl",
            "streams": src.info,
            "settings": {"sign": args.sign, "scale": args.scale, "alpha": args.alpha,
                         "gap_s": GAP_S, "watchdog_s": WATCHDOG_S, "clip_dps": CLIP_DPS},
            "bias": None,
        }
        writer.write_meta(meta)

        markers: queue.Queue = queue.Queue()
        start_marker_thread(markers)

        if args.bias > 0:
            meta["bias"] = measure_bias(src, args.bias, tracker, health, writer)
            tracker.bias = np.asarray(meta["bias"]["vector"])
            tracker.reset(0.0)
            writer.write_meta(meta)
        else:
            print("\nNo bias window (--bias 0): live yaw will drift by the raw gyro bias.")

        print("\nRECORDING. Enter = marker, text + Enter = labelled marker, "
              "r = reset yaw to 0, q = stop, Ctrl+C = stop.")
        t_start = src.clock()
        t_stop = t_start + args.seconds if args.seconds > 0 else math.inf
        next_status = t_start
        outage_since = None
        outages: list[dict] = []
        stop_reason = "time up"

        try:
            while True:
                now = src.clock()
                if now >= t_stop:
                    break
                for t, kind, v in src.poll():
                    handle_item(t, kind, v, tracker, health, writer)

                # typed markers / commands
                while not markers.empty():
                    text = markers.get()
                    low = text.lower()
                    if low in QUIT_WORDS:
                        t_stop = now
                        stop_reason = "stopped by user"
                        break
                    if low in RESET_WORDS:
                        tracker.reset(0.0)
                        writer.marker(now, "reset")
                        print(f"  [{now - t_start:7.1f}s] yaw reset to 0")
                    else:
                        writer.marker(now, text or "mark")
                        print(f"  [{now - t_start:7.1f}s] marker: {text or 'mark'}")

                # Bluetooth watchdog: no gyro data for WATCHDOG_S
                last = src.last_arrival.get("GYRO") or t_start
                silent = now - last
                if silent > WATCHDOG_S and outage_since is None:
                    outage_since = last
                    print(f"  WARNING [{now - t_start:7.1f}s] no gyro data for "
                          f"{silent:.1f} s: Bluetooth dropped?", flush=True)
                elif silent <= WATCHDOG_S and outage_since is not None:
                    dur = last - outage_since
                    outages.append({"at_s": round(outage_since - t_start, 2),
                                    "duration_s": round(dur, 2)})
                    writer.marker(last, f"gyro-back-after-{dur:.1f}s")
                    print(f"  gyro data back after {dur:.1f} s", flush=True)
                    outage_since = None

                if now >= next_status and args.status_every > 0:
                    print(status_line(now - t_start, health, tracker), flush=True)
                    writer.flush()
                    next_status = now + args.status_every
                time.sleep(0.01)
        except KeyboardInterrupt:
            stop_reason = "Ctrl+C"
        if outage_since is not None:
            outages.append({"at_s": round(outage_since - t_start, 2),
                            "duration_s": None, "note": "still out at the end"})

        # ---- summary
        elapsed = src.clock() - t_start
        drift = None
        if meta["bias"] is not None and elapsed > 1:
            drift = {"net_yaw_change_deg": round(tracker.unwrapped, 2),
                     "minutes": round(elapsed / 60.0, 3),
                     "deg_per_min": round(tracker.unwrapped / (elapsed / 60.0), 2)}
        meta["summary"] = {
            "stop_reason": stop_reason,
            "recorded_s": round(elapsed, 2),
            "streams": {k: h.summary() for k, h in health.items()},
            "watchdog_outages": outages,
            "markers": writer.markers,
            "gyro_only_yaw": drift,
        }
        writer.write_meta(meta)

        print(f"\nStopped ({stop_reason}) after {elapsed:.1f} s. Saved to {writer.dir}")
        for h in health.values():
            print("  " + h.summary_line())
        if outages:
            print(f"  Watchdog: {len(outages)} outage(s): "
                  + ", ".join(f"at {o['at_s']}s for {o['duration_s']}s" for o in outages))
        else:
            print("  Watchdog: no outages")
        if drift:
            print(f"  Gyro-only yaw since the last reset: {drift['net_yaw_change_deg']:+.1f} deg over "
                  f"{drift['minutes']:.2f} min = {drift['deg_per_min']:+.2f} deg/min\n"
                  f"    (a drift figure only if you stayed still or ended facing your start direction)")
    finally:
        if writer is not None:
            writer.close()
        if src is not None:
            src.close()
        if proc is not None:
            proc.terminate()

    if args.plot and writer is not None:
        plot_session(writer.dir, show=True)


def _session_settings(session: Path, args) -> tuple[np.ndarray | None, float, float, float]:
    meta_path = session / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    settings = meta.get("settings", {})
    sign = args.sign if args.sign is not None else settings.get("sign", 1.0)
    scale = args.scale if args.scale is not None else settings.get("scale", 1.0)
    alpha = args.alpha if args.alpha is not None else settings.get("alpha", 0.02)
    bias = None
    if getattr(args, "bias_seconds", 0):
        g = np.loadtxt(session / "gyro.csv", delimiter=",", skiprows=1, ndmin=2)
        win = g[g[:, 0] <= g[0, 0] + args.bias_seconds, 1:4]
        bias = win.mean(axis=0)
    elif meta.get("bias"):
        bias = np.asarray(meta["bias"]["vector"], float)
    return bias, sign, scale, alpha


def compute_yaw(session: Path, bias, sign, scale, alpha) -> np.ndarray:
    """Run a whole session through the yaw code instantly. Returns rows of (t, yaw, rate)."""
    src = FileSource(session, speed=0)
    tracker = YawTracker(bias=bias, sign=sign, scale=scale, alpha=alpha)
    bias_end = None
    for t, text in read_markers(session):
        if text == "bias-end":
            bias_end = t
    out = []
    while not src.done:
        for t, kind, v in src.poll():
            handle_item(t, kind, v, tracker, {})
            if bias_end is not None and kind == "GYRO" and t <= bias_end:
                tracker.reset(0.0)      # yaw starts at 0 at the end of the bias window, like live
            if kind == "GYRO":
                out.append((t, tracker.yaw, tracker.rate))
    return np.asarray(out)


def cmd_replay(args) -> None:
    session = Path(args.session)
    bias, sign, scale, alpha = _session_settings(session, args)
    if bias is None:
        print("No bias in meta.json; replaying with zero bias (use --bias-seconds N to "
              "average the first N s).")
    else:
        print(f"bias = {np.round(bias, 3).tolist()} deg/s, sign {sign:+.0f}, scale {scale}")

    if args.save or args.speed <= 0:
        yaw = compute_yaw(session, bias, sign, scale, alpha)
        if args.save:
            path = session / "yaw.csv"
            np.savetxt(path, yaw, delimiter=",", header="t,yaw_deg,rate_dps", comments="",
                       fmt=["%.6f", "%.3f", "%.3f"])
            print(f"Wrote {path} ({len(yaw)} rows)")
        print(f"Final yaw {yaw[-1, 1]:+.1f} deg after {yaw[-1, 0] - yaw[0, 0]:.1f} s")
        return

    # real-time replay through exactly the same per-sample path as a live run
    src = FileSource(session, speed=args.speed)
    tracker = YawTracker(bias=bias, sign=sign, scale=scale, alpha=alpha)
    health = {"GYRO": StreamHealth("GYRO"), "ACC": StreamHealth("ACC")}
    bias_end = next((t for t, text in read_markers(session) if text == "bias-end"), None)
    t0, next_status = src.t0, 0.0
    print(f"Replaying {session} at {args.speed}x. Ctrl+C to stop.")
    try:
        while not src.done:
            for t, kind, v in src.poll():
                handle_item(t, kind, v, tracker, health)
                if bias_end is not None and kind == "GYRO" and t <= bias_end:
                    tracker.reset(0.0)
                if kind == "MARK" and not v.startswith("bias-"):
                    print(f"  [{t - t0:7.1f}s] marker: {v}")
            elapsed = src.clock() - t0
            if elapsed >= next_status:
                print(status_line(elapsed, health, tracker), flush=True)
                next_status = elapsed + args.status_every
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    print("\n" + "\n".join("  " + h.summary_line() for h in health.values()))


def plot_session(session: Path, show: bool = True, args=None) -> Path:
    import matplotlib
    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    session = Path(session)

    class _A:  # defaults when called straight after recording
        sign = scale = alpha = None
        bias_seconds = 0
    bias, sign, scale, alpha = _session_settings(session, args or _A())

    g = np.loadtxt(session / "gyro.csv", delimiter=",", skiprows=1, ndmin=2)
    a_path = session / "acc.csv"
    a = np.loadtxt(a_path, delimiter=",", skiprows=1, ndmin=2) if a_path.exists() else None
    yaw = compute_yaw(session, bias, sign, scale, alpha)
    t0 = g[0, 0]
    marks = [(t - t0, text) for t, text in read_markers(session)]

    fig, ax = plt.subplots(3, 1, sharex=True, figsize=(12, 8))
    for i, name in enumerate(["gx", "gy", "gz"]):
        ax[0].plot(g[:, 0] - t0, g[:, i + 1], lw=0.8, label=name)
    for lim in (GYRO_LIMIT_DPS, -GYRO_LIMIT_DPS):
        ax[0].axhline(lim, color="grey", ls=":", lw=0.8)
    ax[0].set_ylabel("gyro (deg/s)")
    ax[0].legend(loc="upper right", ncol=3)
    if a is not None:
        for i, name in enumerate(["ax", "ay", "az"]):
            ax[1].plot(a[:, 0] - t0, a[:, i + 1], lw=0.8, label=name)
        ax[1].legend(loc="upper right", ncol=3)
    ax[1].set_ylabel("accel (g)")
    ax[2].plot(yaw[:, 0] - t0, yaw[:, 1], lw=1.0, color="black")
    ax[2].set_ylabel("yaw (deg)")
    ax[2].set_ylim(-185, 185)
    ax[2].set_xlabel("time (s)")
    for t, text in marks:
        for axis in ax:
            axis.axvline(t, color="tab:red", lw=0.6, alpha=0.5)
        ax[0].annotate(text, (t, ax[0].get_ylim()[1]), fontsize=7, rotation=90,
                       va="top", ha="right", color="tab:red")
    for axis in ax:
        axis.grid(alpha=0.3)
    fig.suptitle(f"{session.name}   (bias {'removed' if bias is not None else 'NOT removed'}, "
                 f"sign {sign:+.0f}, scale {scale})")
    fig.tight_layout()
    out = session / "plot.png"
    fig.savefig(out, dpi=120)
    print(f"Saved {out}")
    if show:
        plt.show()
    plt.close(fig)
    return out


def cmd_plot(args) -> None:
    plot_session(Path(args.session), show=not args.no_show, args=args)


def cmd_list(args) -> None:
    lsl = _import_pylsl()
    print(f"Looking for LSL streams for {args.wait:.0f} s...")
    streams = lsl.resolve_streams(wait_time=args.wait)
    if not streams:
        print("None found. Start `muselsl stream --gyro --acc` first.")
        return
    for s in streams:
        print(f"  type={s.type():<6} name={s.name():<12} rate={s.nominal_srate():>5.0f} Hz "
              f"channels={s.channel_count()} source_id={s.source_id()}")


# ----------------------------------------------------------------------------- CLI
def main(argv=None) -> None:
    p = argparse.ArgumentParser(
        description="SightLine Muse S (Gen 2) head-motion data collection.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("EXAMPLES")[1]
        if "EXAMPLES" in __doc__ else None)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="record gyro + accel (and optionally EEG/PPG)")
    r.add_argument("--label", default="session", help="short name, e.g. D1-desk")
    r.add_argument("--notes", default="", help="free text saved in meta.json")
    r.add_argument("--seconds", type=float, default=0,
                   help="recording length after the bias window (0 = until q / Ctrl+C)")
    r.add_argument("--bias", type=float, default=5.0,
                   help="seconds of standing still to measure gyro bias first (0 = skip)")
    r.add_argument("--sign", type=float, default=1.0, choices=[1.0, -1.0],
                   help="-1 if a LEFT turn shows as negative yaw (find it with the axis test)")
    r.add_argument("--scale", type=float, default=1.0,
                   help="turn-accuracy correction factor from the 45-degree tape test")
    r.add_argument("--alpha", type=float, default=0.02,
                   help="tilt-fix smoothing per accel sample (0.02 ~ 1 s; try 0.05 / 0.01)")
    r.add_argument("--eeg", action="store_true", help="also record EEG (for Jordan later)")
    r.add_argument("--ppg", action="store_true", help="also record PPG")
    r.add_argument("--out", default="data", help="folder for sessions (default: data)")
    r.add_argument("--launch", action="store_true",
                   help="start `muselsl stream --gyro --acc` for you, and stop it at the end")
    r.add_argument("--address", default=None, help="Muse MAC address (with --launch)")
    r.add_argument("--timeout", type=float, default=10.0, help="seconds to wait for streams")
    r.add_argument("--status-every", type=float, default=1.0,
                   help="seconds between live status lines (0 = quiet)")
    r.add_argument("--gyro-type", default="GYRO", help="LSL type of the gyro stream")
    r.add_argument("--acc-type", default="ACC", help="LSL type of the accel stream")
    r.add_argument("--plot", action="store_true", help="plot the session when done")
    r.set_defaults(func=cmd_record)

    for name, func, helptext in (("replay", cmd_replay, "play a session back through the yaw code"),
                                 ("plot", cmd_plot, "plot gyro, accel and yaw for a session")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("session", help="session folder, e.g. data/2026-11-07_101500_D1-desk")
        s.add_argument("--sign", type=float, default=None, choices=[1.0, -1.0],
                       help="override the sign saved with the session")
        s.add_argument("--scale", type=float, default=None, help="override the scale factor")
        s.add_argument("--alpha", type=float, default=None, help="override tilt smoothing")
        s.add_argument("--bias-seconds", type=float, default=0,
                       help="compute bias from the first N s instead of meta.json")
        if name == "replay":
            s.add_argument("--speed", type=float, default=1.0,
                           help="1 = real time, 4 = 4x, 0 = instant")
            s.add_argument("--save", action="store_true", help="write yaw.csv into the session")
            s.add_argument("--status-every", type=float, default=1.0)
        else:
            s.add_argument("--no-show", action="store_true", help="save plot.png without a window")
        s.set_defaults(func=func)

    ls = sub.add_parser("list", help="list the LSL streams this laptop can see")
    ls.add_argument("--wait", type=float, default=5.0)
    ls.set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
