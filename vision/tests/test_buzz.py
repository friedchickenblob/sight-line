"""PhoneBuzzer: continuous on/off buzz, and pulse mode (one notification every N s) while the worker is in view and not facing a hazard, only with a phone connected."""

import threading

from vision.buzz import PhoneBuzzer


def msg(facing, worker=True, dist=1.5, rel=90.0):
    return {"worker": {"track_id": 1} if worker else None,
            "hazards": [] if facing == "none" else [{"label": "toy car", "distance_m": dist, "rel_bearing_deg": rel,
                                                    "facing": facing}]}


class Rig:
    def __init__(self, phones=1):
        self.t, self.phones, self.sent = 0.0, phones, []
        self.done = threading.Event()

    def send(self, payload):
        self.sent.append(payload)
        self.done.set()
        return self.phones

    def make(self):
        return PhoneBuzzer(mode="pulse", buzz_send=lambda p: 0, every_s=5, clients=lambda: self.phones, send=self.send, clock=lambda: self.t)

    def feed(self, b, m):
        self.done.clear()
        b.update(m)
        self.done.wait(0.3)               # the HTTP part runs on the buzzer's thread


def test_buzzes_when_not_facing_then_every_5_seconds():
    r = Rig()
    b = r.make()
    r.feed(b, msg(False))
    assert len(r.sent) == 1
    for t in (1.0, 4.9):                  # too soon
        r.t = t
        r.feed(b, msg(False))
    assert len(r.sent) == 1
    r.t = 5.0
    r.feed(b, msg(False))
    assert len(r.sent) == 2


def test_message_says_what_and_where():
    r = Rig()
    r.feed(r.make(), msg(False, dist=1.2, rel=-80.0))
    p = r.sent[0]
    assert p["v"] == 1 and p["level"] == "warn"          # short buzz by default: stops sooner
    assert "toy car" in p["message"] and "1.2 m" in p["message"] and "80 deg to your right" in p["message"]


def test_silent_when_facing_unknown_no_worker_or_no_hazard():
    r = Rig()
    b = r.make()
    for m in (msg(True), msg(None), msg(False, worker=False), msg("none")):
        r.feed(b, m)
    assert r.sent == []


def test_no_buzz_without_a_connected_phone():
    r = Rig(phones=0)
    b = r.make()
    r.feed(b, msg(False))
    assert r.sent == []
    r.t, r.phones = 5.0, 1                # a phone connects: the next buzz goes out
    r.feed(b, msg(False))
    assert len(r.sent) == 1


def test_picks_the_nearest_unseen_hazard():
    m = {"worker": {"track_id": 1},
         "hazards": [{"label": "far", "distance_m": 3.0, "facing": False},
                     {"label": "near", "distance_m": 1.0, "facing": False},
                     {"label": "seen", "distance_m": 0.5, "facing": True}]}
    assert PhoneBuzzer.unseen_hazard(m)["label"] == "near"


def test_settings_change_while_running():
    sent, t = [], [0.0]
    b = PhoneBuzzer(mode="pulse", buzz_send=lambda p: 0, every_s=1, clients=lambda: 1, send=lambda p: sent.append(p) or 1, clock=lambda: t[0])
    msg = {"worker": {"x_m": 0}, "hazards": [{"label": "toy car", "facing": False, "distance_m": 1.0}]}
    assert b.every_s == 1
    b.update(msg); t[0] = 0.5; b.update(msg); t[0] = 1.0; b.update(msg)
    assert b._work.qsize() <= 1                     # queued, drained by the thread
    b.configure(enabled=False)
    last = b._last; t[0] = 5.0; b.update(msg)
    assert b._last == last                          # off: no buzz
    s = b.configure(every_s=0.01, enabled=True)
    assert s["every_s"] == PhoneBuzzer.MIN_EVERY_S and s["enabled"]
    assert b.configure(every_s=10_000)["every_s"] == PhoneBuzzer.MAX_EVERY_S


def test_queued_buzz_is_dropped_once_the_worker_faces_the_hazard():
    gate, sent, t = threading.Event(), [], [0.0]

    def clients():
        gate.wait(1)                                   # hold the first buzz "in flight"
        return 1

    b = PhoneBuzzer(mode="pulse", buzz_send=lambda p: 0, every_s=0.2, clients=clients, send=lambda p: sent.append(p) or 1, clock=lambda: t[0])
    b.update(msg(False))                              # buzz 1 goes to the thread and blocks in clients()
    t[0] = 0.3
    b.update(msg(False))                              # buzz 2 queued behind it
    b.update(msg(True))                               # ...then the worker looks at the car
    gate.set()
    import time; time.sleep(0.3)
    assert sent == []                                 # neither buzz went out after they looked
    assert b.dropped >= 1


def test_stale_buzz_is_dropped_and_level_is_settable():
    b = PhoneBuzzer(mode="pulse", buzz_send=lambda p: 0, every_s=1, clients=lambda: 1, send=lambda p: 1)
    assert b.configure(level="alert")["level"] == "alert"
    import pytest
    with pytest.raises(ValueError):
        b.configure(level="loud")


# ------------------------------------------------------------------ continuous mode
import time as _time


class Cont:
    """A continuous-mode buzzer with a fake clock and a fake /buzz endpoint."""

    def __init__(self):
        self.t, self.sent = 0.0, []
        self.b = PhoneBuzzer(clients=lambda: 1, send=lambda p: 1, buzz_send=self.post, clock=lambda: self.t)

    def post(self, payload):
        self.sent.append(payload)
        return 1

    def feed(self, m, wait=0.25):
        self.b.update(m)
        _time.sleep(wait)                     # let the sender thread react

    def ons(self):
        return [p for p in self.sent if p["on"]]


def test_continuous_starts_at_once_and_stops_at_once():
    c = Cont()
    c.feed(msg(False, dist=1.2, rel=40.0))
    assert c.sent and c.sent[0]["on"] is True and c.sent[0]["hold_s"] == PhoneBuzzer.HOLD_S
    assert "Look at the toy car" == c.sent[0]["title"] and "left" in c.sent[0]["message"]
    assert c.b.buzzing
    c.feed(msg(True))
    assert c.sent[-1] == {"v": 1, "on": False} and not c.b.buzzing


def test_continuous_refreshes_while_it_lasts_and_not_every_frame():
    c = Cont()
    for i in range(10):                       # 10 frames inside one refresh period
        c.t = i * 0.04
        c.b.update(msg(False))
    _time.sleep(0.25)
    assert len(c.ons()) == 1
    c.t = 1.0                                 # well past REFRESH_S since the first "on"
    c.feed(msg(False))
    assert len(c.ons()) == 2


def test_continuous_stops_when_frames_stop_arriving():
    c = Cont()
    c.feed(msg(False))
    assert c.b.buzzing
    c.t = 1.0                                 # no update for longer than FRAME_STALE_S
    _time.sleep(0.25)
    assert c.sent[-1]["on"] is False and not c.b.buzzing


def test_continuous_heading_lost_or_no_worker_never_buzzes():
    c = Cont()
    c.feed(msg(None))
    c.feed(msg(False, worker=False))
    assert c.sent == []


def test_turning_buzz_off_or_switching_mode_stops_the_phone():
    c = Cont()
    c.feed(msg(False))
    c.b.configure(enabled=False)
    _time.sleep(0.25)
    assert c.sent[-1]["on"] is False
    c.b.configure(enabled=True)
    c.feed(msg(False))
    assert c.b.buzzing
    c.b.configure(mode="pulse")
    c.feed(msg(False))
    assert c.sent[-1]["on"] is False and not c.b.buzzing
