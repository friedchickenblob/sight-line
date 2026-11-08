"""The worker's heading from the Muse S, for the vision pipeline.

Runs Max's head-motion code (max/headmotion: BrainFlow -> bias + tilt fix -> yaw on the floor
frame) in a background thread and keeps the latest yaw, so the camera loop can read it every
frame without waiting on Bluetooth.

    heading = MuseHeading()                       # Muse S over the laptop's Bluetooth
    heading = MuseHeading("synthetic:joint")      # fake head motion, no headband
    heading.start()
    heading.yaw_deg                               # None until calibrated / when data stops
    heading.zero()                                # worker now faces +x (toward floor marker 1)

Startup: the worker stands still for `bias_s` seconds (gyro bias), and yaw starts at 0 deg,
i.e. it assumes they face +x then. Re-zero (zero(), the z key) whenever they face +x again.
The Muse has no magnetometer, so the heading is relative to that zero and slowly drifts.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

MAX_DIR = Path(__file__).resolve().parent.parent / "max"


def _import_headmotion():
    if str(MAX_DIR) not in sys.path:
        sys.path.insert(0, str(MAX_DIR))
    from headmotion.calibration import Calibration
    from headmotion.health import StreamHealth
    from headmotion.sources import RESET, Sample, open_source
    from headmotion.yaw import HeadYawEstimator, YawPipeline
    return Calibration, StreamHealth, RESET, Sample, open_source, HeadYawEstimator, YawPipeline


class MuseHeading:
    """source: "muse" (BrainFlow), "replay:<csv>" or "synthetic:<scene>" (max/headmotion/sources.py).
    muse_kw: board ("muse_s" | "muse_s_bled" | "synthetic"), name, mac, serial_port, timeout_s.
    flip: multiply yaw by -1 (until the axis/sign experiment fills max/calibration.json, a left
    turn may read negative; the floor frame wants left = +)."""

    STALE_S = 2.0          # no new yaw for this long -> yaw_deg is None (don't judge "facing" on old data)

    def __init__(self, source: str = "muse", bias_s: float = 5.0, flip: bool = False, **muse_kw):
        self.source, self.bias_s, self.flip = source, bias_s, flip
        self.muse_kw = {k: v for k, v in muse_kw.items() if v is not None}
        self._yaw: float | None = None
        self._valid = False
        self._t_update = 0.0                    # time.monotonic() of the last yaw
        self._zero = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = "starting"                 # starting | connecting | hold still | running | error: ...
        self.rate_hz = 0.0
        self.events: list[str] = []

    # ---------------------------------------------------------------- public
    def start(self) -> "MuseHeading":
        self._thread = threading.Thread(target=self._run, name="muse-heading", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def zero(self) -> None:
        """The worker is facing +x right now: set the heading to 0."""
        self._zero.set()

    @property
    def yaw_deg(self) -> float | None:
        if self._yaw is None or time.monotonic() - self._t_update > self.STALE_S:
            return None
        return -self._yaw if self.flip else self._yaw

    @property
    def status(self) -> str:
        """One short line for the overlay."""
        if self.state == "running":
            if time.monotonic() - self._t_update > self.STALE_S:
                return "muse: NO DATA"
            return f"muse {self.rate_hz:.0f} Hz" + ("" if self._valid else " (not valid)")
        return f"muse: {self.state}"

    # ---------------------------------------------------------------- thread
    def _log(self, msg: str) -> None:
        self.events.append(msg)
        print(f"[muse] {msg}", flush=True)

    def _run(self) -> None:
        try:
            Calibration, StreamHealth, RESET, Sample, open_source, HeadYawEstimator, YawPipeline = _import_headmotion()
        except Exception as e:                  # brainflow / pylsl not installed
            self.state = f"error: {e}"
            self._log(self.state)
            return
        cal = Calibration.load()
        live = self.source == "muse"
        self.state = "connecting"
        self._log(f"opening {self.source}" + (f" {self.muse_kw}" if self.muse_kw else ""))
        tries = 0
        while True:                             # headband off / out of range: keep trying until it appears
            try:
                src = open_source(self.source, realtime=not live, **(self.muse_kw if live else {}))
                break
            except Exception as e:
                tries += 1
                self.state = "not found, retrying" if live else f"error: {e}"
                if tries == 1 or not live:
                    self._log(f"{e}" + ("; retrying every few seconds" if live else ""))
                if not live or self._stop.wait(3.0):
                    return
        est = HeadYawEstimator(cal)
        pipe = YawPipeline(est, self.bias_s, start_yaw=0.0, retry_bias=True)
        health = StreamHealth()
        self.state = "hold still"
        self._log(f"connected; worker: hold still for {self.bias_s:.0f} s facing +x (toward floor marker 1)")
        shown = 0
        try:
            for batch in src.batches():
                if self._stop.is_set():
                    break
                if self._zero.is_set() and pipe.running:
                    self._zero.clear()
                    batch = batch + [Sample(RESET, src.now(), (0.0,))]
                for s in batch:
                    health.feed(s)
                    out = pipe.feed(s)
                    if out is not None and out.yaw_deg is not None:
                        self._yaw, self._valid, self._t_update = out.yaw_deg, out.valid, time.monotonic()
                if pipe.running and self.state != "running":
                    self.state = "running"
                for e in pipe.events[shown:]:
                    self._log(e)
                shown = len(pipe.events)
                while live and getattr(src, "events", None):
                    self._log(src.events.popleft())
                h = health.maybe_report(src.now(), 1.0)
                if h is not None:
                    self.rate_hz = h.rate_hz
            else:
                self._log("source ended")
        except Exception as e:
            self.state = f"error: {e}"
            self._log(self.state)
        finally:
            if live:
                src.close()
