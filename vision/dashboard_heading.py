"""The worker's heading, read from the running head-motion dashboard (max/dashboard) instead of from the Muse.

The Muse S can only be held by one program. When the dashboard owns it, this takes the same yaw the
dashboard shows, so the Dashboard tab and the Vision overlay always agree:

    heading = DashboardHeading("http://127.0.0.1:5000").start()
    heading.yaw_deg          # None until the dashboard has calibrated / when its data stops
    heading.zero()           # same as the dashboard's re-zero button

It has the same interface as vision/head_yaw.py's MuseHeading, so the pipeline treats them alike.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:5000"


class DashboardHeading:
    STALE_S = 2.0          # the dashboard's yaw hasn't changed for this long -> yaw_deg is None
    POLL_S = 0.1

    def __init__(self, url: str = DEFAULT_URL, flip: bool = False, fetch=None):
        self.url, self.flip = url.rstrip("/"), flip
        self._fetch = fetch or self._http_state
        self._yaw: float | None = None
        self._valid = False
        self._t_dash: float | None = None        # the dashboard's own timestamp of that yaw
        self._t_update = 0.0                     # time.monotonic() when it last changed
        self.state = "starting"                  # starting | no dashboard | connected | hold still | running
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- public
    def start(self) -> "DashboardHeading":
        self._thread = threading.Thread(target=self._run, name="dashboard-heading", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def zero(self) -> None:
        """The worker is facing +x right now: ask the dashboard to re-zero."""
        req = urllib.request.Request(self.url + "/api/rezero", method="POST", data=b"")
        try:
            urllib.request.urlopen(req, timeout=2).close()
        except (urllib.error.URLError, OSError) as e:
            print(f"[dashboard] re-zero failed: {e}", flush=True)

    @property
    def yaw_deg(self) -> float | None:
        if self._yaw is None or time.monotonic() - self._t_update > self.STALE_S:
            return None
        return -self._yaw if self.flip else self._yaw

    @property
    def status(self) -> str:
        if self.state in ("running", "connected") and self._yaw is not None:
            if time.monotonic() - self._t_update > self.STALE_S:
                return "dashboard muse: NO DATA"
            return "dashboard muse" + ("" if self._valid else " (not valid)")
        return f"dashboard muse: {self.state}"

    # ---------------------------------------------------------------- thread
    def _http_state(self) -> dict:
        with urllib.request.urlopen(self.url + "/api/state", timeout=2) as r:
            return json.load(r)

    def poll_once(self) -> None:
        """One look at the dashboard's state (the thread calls this; tests can too)."""
        try:
            st = self._fetch()
        except (urllib.error.URLError, OSError, ValueError):
            self.state = "no dashboard"
            return
        y = st.get("yaw")
        if not (st.get("health") or {}).get("connected", True) or y is None or y.get("yaw_deg") is None:
            self.state = "hold still" if st.get("bias") is None else "connected"
            return
        self.state = "running"
        self._valid = bool(y.get("valid", True))
        if y.get("t_s") != self._t_dash or self._yaw is None:      # a new estimate, not the same one again
            self._t_dash, self._t_update = y.get("t_s"), time.monotonic()
        self._yaw = float(y["yaw_deg"])

    def _run(self) -> None:
        last = None
        while not self._stop.wait(self.POLL_S):
            self.poll_once()
            if self.state != last:
                print(f"[dashboard] {self.url}: {self.state}", flush=True)
                last = self.state
