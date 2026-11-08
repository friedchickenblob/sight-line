"""PhoneBuzzer: buzz every 5 s while the worker is in view and not facing a hazard, only with a phone connected."""

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
        return PhoneBuzzer(every_s=5, clients=lambda: self.phones, send=self.send, clock=lambda: self.t)

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
    assert p["v"] == 1 and p["level"] == "alert"
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
