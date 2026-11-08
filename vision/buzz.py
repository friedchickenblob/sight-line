"""Buzz the phone while the worker is not looking at a hazard.

Fed with each frame's hazard message (vision/pipeline.py scene_to_dict). It buzzes while a worker is in view and
some hazard has "alert": true, i.e. the hazard is moving toward the worker ("approaching": its direction of
travel is within the car heading cone of the worker) AND the worker isn't facing it ("facing": false).
Unknown (null) never buzzes. Messages without "alert" fall back to "facing": false alone.

Two modes (`mode`, changeable while it runs from the overlay page's Buzz controls):

continuous (default, needs the phone-alert app 1.1+)
    POST /buzz {"on": true, "hold_s": HOLD_S} to the phone-alert server the moment the worker stops facing
    the hazard, refreshed every REFRESH_S while it lasts, and {"on": false} the moment they face it. The app
    plays its sound + vibration on loop in between, so start and stop are both as quick as a frame plus
    the network (~0.1-0.3 s). Fail-safes: if frames stop arriving for FRAME_STALE_S the buzz is switched
    off, and if the laptop goes silent the app stops by itself after HOLD_S.

pulse (older apps)
    One notification (POST /notify) every `every_s` seconds, only if a phone is connected. The phone can't
    cancel a pulse once it has started, so it ends up to its own length after the worker looks:
    level "warn" = the app's 0.75 s buzz, "alert" = 2.2 s. A pulse is dropped at send time if the worker
    faced the hazard since it was queued, and expires on the server after TTL_S.

    buzzer = PhoneBuzzer("http://127.0.0.1:8765")
    buzzer.update(message)        # once per frame, returns immediately
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8765"


def no_localhost(url: str) -> str:
    """'localhost' -> 127.0.0.1. On Windows, localhost tries IPv6 (::1) first; the alert server listens on
    IPv4 only, so every request waited ~2 s for that to fail. That was the buzz 'backlog'."""
    return url.replace("://localhost", "://127.0.0.1")


class PhoneBuzzer:
    MIN_EVERY_S, MAX_EVERY_S = 0.2, 600.0
    MODES = ("continuous", "pulse")
    LEVELS = ("warn", "alert")             # pulse mode, phone-alert app: warn = 0.75 s buzz, alert = 2.2 s buzz
    STALE_S = 0.5                          # pulse mode: a queued pulse older than this is dropped
    TTL_S = 1.0                            # pulse mode: the alert server won't replay it after this
    REFRESH_S = 0.5                        # continuous: resend "on" this often while it lasts
    HOLD_S = 1.5                           # continuous: the app stops by itself if no "on" for this long
    FRAME_STALE_S = 0.5                    # continuous: no frame for this long (stream stopped) -> off

    def __init__(self, url: str = DEFAULT_URL, every_s: float = 1.0, token: str | None = None,
                 clients=None, send=None, buzz_send=None, clock=time.monotonic, mode: str = "continuous"):
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}")
        self.url, self.token, self.clock, self.mode = no_localhost(url).rstrip("/"), token, clock, mode
        self.every_s = self.clamp(every_s)
        self.enabled = True
        self.level = "warn"
        self.sent = 0
        self._warned = None
        # pulse mode
        self._away = False                                  # latest frame: worker not facing some hazard
        self._away_seq = 0                                  # bumps each time they turn back to face it
        self.dropped = 0
        self._clients = clients or self._http_clients       # () -> number of connected phones
        self._send = send or self._http_send                # (payload) -> number of phones it reached
        self._last = float("-inf")
        self._work: queue.Queue = queue.Queue(maxsize=1)    # HTTP happens here, never on the camera thread
        # continuous mode
        self._buzz_send = buzz_send or self._http_buzz      # (payload) -> number of phones it reached
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._want_on = False
        self._want_payload: dict | None = None
        self._last_frame = float("-inf")
        self.buzzing = False                                # what the phone was last told
        threading.Thread(target=self._run_pulse, name="phone-buzzer", daemon=True).start()
        threading.Thread(target=self._run_continuous, name="phone-buzz-state", daemon=True).start()

    # ---------------------------------------------------------------- settings
    @staticmethod
    def unseen_hazard(msg: dict) -> dict | None:
        """The nearest hazard to warn about, or None: "alert" true (heading at the worker and not faced), or,
        for messages without "alert", just "facing" false."""
        if not msg.get("worker"):
            return None
        away = [h for h in msg.get("hazards", [])
                if (h.get("alert") is True if "alert" in h else h.get("facing") is False)]
        return min(away, key=lambda h: h.get("distance_m") if h.get("distance_m") is not None else float("inf"), default=None)

    @classmethod
    def clamp(cls, every_s: float) -> float:
        return min(max(float(every_s), cls.MIN_EVERY_S), cls.MAX_EVERY_S)

    def settings(self) -> dict:
        return {"enabled": self.enabled, "mode": self.mode, "every_s": self.every_s, "level": self.level,
                "buzzing": self.buzzing, "sent": self.sent, "dropped_stale": self.dropped,
                "min_every_s": self.MIN_EVERY_S, "max_every_s": self.MAX_EVERY_S, "problem": self._warned}

    def configure(self, every_s: float | None = None, enabled: bool | None = None, level: str | None = None,
                  mode: str | None = None) -> dict:
        if level is not None:
            if level not in self.LEVELS:
                raise ValueError(f"level must be one of {self.LEVELS}")
            self.level = level
        if mode is not None:
            if mode not in self.MODES:
                raise ValueError(f"mode must be one of {self.MODES}")
            self.mode = mode
        if every_s is not None:
            self.every_s = self.clamp(every_s)
        if enabled is not None:
            self.enabled = bool(enabled)
        self._wake.set()                                    # e.g. turned off mid-buzz: stop the phone now
        return self.settings()

    # ---------------------------------------------------------------- per frame
    def update(self, msg: dict) -> None:
        h = self.unseen_hazard(msg) if self.enabled else None
        now = self.clock()
        with self._lock:                                    # continuous: just record the wanted state
            self._last_frame = now
            on = h is not None and self.mode == "continuous"
            if on:
                self._want_payload = self.buzz_payload(h, self.HOLD_S)
            if on != self._want_on:
                self._want_on = on
                self._wake.set()                            # send the change right away
        self._update_pulse(h if self.mode == "pulse" else None, now)

    @staticmethod
    def describe(h: dict) -> tuple[str, str]:
        where = ""
        if h.get("distance_m") is not None:
            where += f" {h['distance_m']:.1f} m away"
        rel = h.get("rel_bearing_deg")
        if rel is not None:
            where += f", {abs(rel):.0f} deg to your {'left' if rel > 0 else 'right'}"
        label = h.get("label", "hazard")
        return f"Look at the {label}", f"{label}{where}".strip()

    @classmethod
    def buzz_payload(cls, h: dict, hold_s: float) -> dict:
        title, message = cls.describe(h)
        return {"v": 1, "on": True, "hold_s": hold_s, "title": title, "message": message}

    @classmethod
    def payload(cls, h: dict, ttl_s: float, level: str = "warn") -> dict:
        title, message = cls.describe(h)
        return {"v": 1, "title": title, "level": level, "message": message, "ttl_s": ttl_s}

    # ---------------------------------------------------------------- continuous
    def _run_continuous(self) -> None:
        last_on = float("-inf")
        while True:
            self._wake.wait(0.1)
            self._wake.clear()
            now = self.clock()
            with self._lock:
                fresh = now - self._last_frame <= self.FRAME_STALE_S
                want = self._want_on and fresh and self.enabled and self.mode == "continuous"
                payload = self._want_payload
            try:
                if want and (not self.buzzing or now - last_on >= self.REFRESH_S):
                    n = self._buzz_send(payload)
                    if not self.buzzing:
                        print(f"[buzz] on: {payload['message']} -> {n} phone(s)", flush=True)
                    self.buzzing, last_on = True, now
                    self.sent += 1
                    self._say(None if n else "no phone connected to the alert server: not buzzing")
                elif not want and self.buzzing:
                    self._buzz_send({"v": 1, "on": False})
                    self.buzzing = False
                    print("[buzz] off", flush=True)
            except (urllib.error.URLError, OSError, ValueError) as e:
                self._say(f"alert server not reachable at {self.url}: {e}")   # retried on the next pass

    # ---------------------------------------------------------------- pulse
    def _update_pulse(self, h: dict | None, now: float) -> None:
        if self._away and h is None:
            self._away_seq += 1                             # they're facing it (or buzzing is off): cancel queued
        self._away = h is not None
        if h is None or now - self._last < self.every_s:
            return
        self._last = now
        try:
            self._work.put_nowait((now, self._away_seq, self.payload(h, self.TTL_S, self.level)))
        except queue.Full:
            pass                                            # the last pulse is still being sent

    def _run_pulse(self) -> None:
        while True:
            queued_at, seq, payload = self._work.get()
            try:
                if self._stale(queued_at, seq):
                    continue
                if self._clients() <= 0:
                    self._say("no phone connected to the alert server: not buzzing")
                    continue
                if self._stale(queued_at, seq):                 # checked again: they may have turned meanwhile
                    continue
                n = self._send(payload)
                self.sent += 1
                self._say(None)
                print(f"[buzz] {payload['message']} -> {n} phone(s)", flush=True)
            except (urllib.error.URLError, OSError, ValueError) as e:
                self._say(f"alert server not reachable at {self.url}: {e}")

    def _stale(self, queued_at: float, seq: int) -> bool:
        """The worker faced the hazard since this pulse was queued, or it waited too long: drop it."""
        if seq != self._away_seq or not self._away or self.clock() - queued_at > self.STALE_S:
            self.dropped += 1
            return True
        return False

    # ---------------------------------------------------------------- HTTP
    def _say(self, problem: str | None) -> None:
        if problem != self._warned:                          # print a problem once, not on every send
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

    def _http_buzz(self, payload: dict) -> int:
        return int(self._request("/buzz", payload).get("delivered_to", 0))
