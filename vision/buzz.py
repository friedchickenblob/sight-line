"""Buzz the phone while the worker is not looking at a hazard.

Fed with each frame's hazard message (vision/pipeline.py scene_to_dict). While a worker is in view and
some hazard has "facing": false, it sends one notification to the phone-alert server (phone-alert/server,
POST /notify) every `every_s` seconds, and only if a phone is connected to that server.
"facing": null (heading not ready or lost) never buzzes.

    buzzer = PhoneBuzzer("http://localhost:8765")
    buzzer.update(message)        # once per frame, returns immediately
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://localhost:8765"


class PhoneBuzzer:
    def __init__(self, url: str = DEFAULT_URL, every_s: float = 5.0, token: str | None = None,
                 clients=None, send=None, clock=time.monotonic):
        self.url, self.every_s, self.token, self.clock = url.rstrip("/"), every_s, token, clock
        self._clients = clients or self._http_clients       # () -> number of connected phones
        self._send = send or self._http_send                # (payload) -> number of phones it reached
        self._last = float("-inf")
        self._work: queue.Queue = queue.Queue(maxsize=1)    # HTTP happens here, never on the camera thread
        self._warned = None
        self.sent = 0
        threading.Thread(target=self._run, name="phone-buzzer", daemon=True).start()

    # ---------------------------------------------------------------- per frame
    @staticmethod
    def unseen_hazard(msg: dict) -> dict | None:
        """The nearest hazard the worker is not facing, or None (no worker, nothing in view, or facing it)."""
        if not msg.get("worker"):
            return None
        away = [h for h in msg.get("hazards", []) if h.get("facing") is False]
        return min(away, key=lambda h: h.get("distance_m") if h.get("distance_m") is not None else float("inf"), default=None)

    def update(self, msg: dict) -> None:
        h = self.unseen_hazard(msg)
        now = self.clock()
        if h is None or now - self._last < self.every_s:
            return
        self._last = now
        try:
            self._work.put_nowait(self.payload(h, self.every_s))
        except queue.Full:
            pass                                            # the last buzz is still being sent

    @staticmethod
    def payload(h: dict, ttl_s: float) -> dict:
        where = ""
        if h.get("distance_m") is not None:
            where += f" {h['distance_m']:.1f} m away"
        rel = h.get("rel_bearing_deg")
        if rel is not None:
            where += f", {abs(rel):.0f} deg to your {'left' if rel > 0 else 'right'}"
        return {"v": 1, "title": f"Look at the {h.get('label', 'hazard')}", "level": "alert",
                "message": f"{h.get('label', 'hazard')}{where}".strip(), "ttl_s": ttl_s}

    # ---------------------------------------------------------------- thread
    def _run(self) -> None:
        while True:
            payload = self._work.get()
            try:
                if self._clients() <= 0:
                    self._say("no phone connected to the alert server: not buzzing")
                    continue
                n = self._send(payload)
                self.sent += 1
                self._say(None)
                print(f"[buzz] {payload['message']} -> {n} phone(s)", flush=True)
            except (urllib.error.URLError, OSError, ValueError) as e:
                self._say(f"alert server not reachable at {self.url}: {e}")

    def _say(self, problem: str | None) -> None:
        if problem != self._warned:                          # print a problem once, not every 5 s
            self._warned = problem
            if problem:
                print(f"[buzz] {problem}", flush=True)

    def _request(self, path: str, data: dict | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Token"] = self.token
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(self.url + path, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.load(r)

    def _http_clients(self) -> int:
        return len(self._request("/api/state").get("clients", []))

    def _http_send(self, payload: dict) -> int:
        return int(self._request("/notify", payload).get("delivered_to", 0))
