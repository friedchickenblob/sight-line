"""BrainFlow input (packet counter, de-jitter, live board) and MQTT messages."""
import json
import time

import numpy as np
import pytest

from headmotion.bus import CameraYawSubscriber, Publisher, parse_host
from headmotion.messages import Contact, HeadYaw, MuseHealth
from headmotion.sources import ACC, CAM, DROP, GYRO, Dejitter, PacketDecoder


def _muse_rows(n_packets, start_pkg=0, skip=(), period=1 / 52, burst=4, seed=0):
    """Rows the way BrainFlow gives them for a Muse: 3 samples per numbered packet,
    stamped on arrival. Packets arrive in Bluetooth bursts of `burst`."""
    rng = np.random.default_rng(seed)
    pkg, ts = [], []
    for k in range(n_packets):
        if k in skip:
            continue
        arrive = (k // burst + 1) * burst * 3 * period + rng.uniform(0, 0.01)   # whole burst lands at once
        for i in range(3):
            pkg.append((start_pkg + k) % 65536)
            ts.append(arrive - (2 - i) * 1e-4)
    n = len(pkg)
    return np.array(pkg, float), np.array(ts), np.zeros((n, 3)) + (0, 0, 1), np.zeros((n, 3))


# ------------------------------------------------------------ PacketDecoder
def test_decoder_emits_samples_and_no_drops_when_complete():
    out = PacketDecoder(52.0).decode(*_muse_rows(20))
    assert [s.kind for s in out[:2]] == [ACC, GYRO]
    assert sum(s.kind == GYRO for s in out) == 60 and not any(s.kind == DROP for s in out)


def test_decoder_counts_a_single_lost_packet():
    """One lost packet is 58 ms: invisible to the guide's 100 ms gap rule, exact from the counter."""
    out = PacketDecoder(52.0).decode(*_muse_rows(30, skip={10}))
    drops = [s for s in out if s.kind == DROP]
    assert len(drops) == 1 and drops[0].v == (3.0,)


def test_decoder_handles_counter_wrap():
    out = PacketDecoder(52.0).decode(*_muse_rows(10, start_pkg=65533))
    assert not any(s.kind == DROP for s in out)


def test_decoder_keeps_state_across_polls():
    pkg, ts, acc, gyro = _muse_rows(20, skip={12})
    dec = PacketDecoder(52.0)
    out = dec.decode(pkg[:31], ts[:31], acc[:31], gyro[:31]) + dec.decode(pkg[31:], ts[31:], acc[31:], gyro[31:])
    assert sum(s.kind == DROP for s in out) == 1 and dec.lost_samples == 3


def test_dejitter_makes_bursty_arrivals_regular():
    pkg, ts, acc, gyro = _muse_rows(600, burst=5)
    t = np.array([s.t for s in PacketDecoder(52.0).decode(pkg, ts, acc, gyro) if s.kind == GYRO])
    raw_dt, dt = np.diff(ts[100:]), np.diff(t[100:])
    assert raw_dt.std() > 0.01                       # bursts: some steps ~0, some ~0.3 s
    assert dt.std() < 0.001 and np.median(dt) == pytest.approx(1 / 52, rel=0.01)
    assert np.all(dt > 0)


def test_dejitter_steps_over_a_lost_packet():
    pkg, ts, acc, gyro = _muse_rows(200, skip={150}, burst=1)
    t = np.array([s.t for s in PacketDecoder(52.0).decode(pkg, ts, acc, gyro) if s.kind == GYRO])
    dt = np.diff(t)
    assert dt.max() == pytest.approx(4 / 52, rel=0.05)   # the gap is where the packet went missing


def test_dejitter_never_steps_backwards():
    d = Dejitter(52.0, warmup=5)
    out = [d.stamp(n, n / 52 + (0.2 if n == 40 else 0.0)) for n in range(100)]
    assert np.all(np.diff(out) > 0)


# ----------------------------------------------- BrainFlow's synthetic board
def test_live_brainflow_synthetic_board():
    """The real BrainFlow code path end to end, with BrainFlow's fake board (no Muse needed)."""
    pytest.importorskip("brainflow")
    from headmotion.sources import LiveMuse
    with LiveMuse(board="synthetic", clock=time.monotonic) as src:
        out, t_end = [], time.monotonic() + 1.5
        while time.monotonic() < t_end:
            out += src.poll()
            time.sleep(0.02)
    g = [s for s in out if s.kind == GYRO]
    t = np.array([s.t for s in g])
    assert len(g) > 200 and np.all(np.diff(t) > 0)
    assert abs(t[-1] - time.monotonic()) < 1.0               # on the local clock, not wall-clock time
    assert all(len(s.v) == 3 for s in g)
    assert src.battery_pct is not None                       # the synthetic board has one; the Muse S doesn't


def test_live_muse_can_start_disconnected_and_search_later():
    """connect=False: no data and no retries until search() finds the board (dashboard button)."""
    pytest.importorskip("brainflow")
    from headmotion.sources import LiveMuse
    with LiveMuse(board="synthetic", clock=time.monotonic, connect=False) as src:
        assert not src.connected
        src._last_data -= 60                                 # long overdue: a connected source would reconnect
        assert not [s for s in src.poll() if s.kind == GYRO]
        assert not src.connected                             # poll() didn't try to connect on its own
        assert src.search() and src.connected and not src.searching
        assert "Muse connected" in src.events
        out, t_end = [], time.monotonic() + 0.5
        while time.monotonic() < t_end:
            out += src.poll()
            time.sleep(0.02)
    assert any(s.kind == GYRO for s in out)


def test_a_failed_read_drops_the_session_and_reconnects_instead_of_ending():
    """A reconnect that BrainFlow reports as fine but whose session is unusable (BOARD_NOT_CREATED on
    the next read) must not end the source: drop it and reconnect after reconnect_after_s."""
    pytest.importorskip("brainflow")
    from brainflow.board_shim import BrainFlowError
    from headmotion.sources import LiveMuse

    class BrokenBoard:
        def get_board_data(self, preset=None):
            raise BrainFlowError("unable to obtain buffer size", 15)

        def stop_stream(self):
            pass

        def release_session(self):
            pass

    with LiveMuse(board="synthetic", clock=time.monotonic) as src:
        src._disconnect()
        src._board = BrokenBoard()
        assert src.poll() == [] and not src.connected          # no exception: session dropped
        assert any("read failed" in e for e in src.events)
        src._last_data -= 60                                   # reconnect is due
        src.poll()
        assert src.connected and "reconnected" in src.events
        out, t_end = [], time.monotonic() + 0.5
        while time.monotonic() < t_end:
            out += src.poll()
            time.sleep(0.02)
    assert any(s.kind == GYRO for s in out)


def test_unknown_board_is_rejected():
    pytest.importorskip("brainflow")
    from headmotion.sources import LiveMuse
    with pytest.raises(ValueError, match="unknown board"):
        LiveMuse(board="muse_9000")


# ------------------------------------------------------------------- MQTT
def test_messages_follow_repo_convention():
    msgs = [HeadYaw(1.0, 10.0, 2.0, "gyro", None, True), Contact(1.0, 2.0, None, 1.5, -180.0, False),
            MuseHealth(1.0, True, 52.0, 0.0)]
    for m in msgs:
        d = json.loads(m.to_json())
        assert d["v"] == 1 and m.TOPIC.startswith("sightline/")
        assert "t_s" in d and "t" not in d
        assert all(k == k.lower() for k in d)
    assert "battery_pct" in msgs[2].as_dict()


class FakeClient:
    def __init__(self):
        self.sent, self.subs = [], []

    def publish(self, topic, payload, qos=0):
        self.sent.append((topic, payload))

    def subscribe(self, topic, qos=0):
        self.subs.append(topic)

    def loop_stop(self):
        pass

    def disconnect(self):
        pass


def test_publisher_sends_json_to_message_topic():
    c = FakeClient()
    Publisher(client=c).publish(HeadYaw(1.0, 10.0, 2.0, "blend", 0.03, True))
    topic, payload = c.sent[0]
    assert topic == "sightline/head_yaw" and json.loads(payload)["yaw_deg"] == 10.0


def test_camera_subscriber_parses_and_filters():
    c = FakeClient()
    sub = CameraYawSubscriber("sightline/head_yaw_cam", client=c)
    sub._on_connect(c, None, None, 0)
    assert c.subs == ["sightline/head_yaw_cam"]

    class Msg:
        def __init__(self, d):
            self.payload = json.dumps(d).encode() if isinstance(d, dict) else d
    for d in ({"v": 1, "t_s": 5.0, "yaw_deg": 30.0}, {"v": 2, "t_s": 5.1, "yaw_deg": 31.0},
              {"v": 1, "t_s": 5.2, "yaw_deg": 32.0, "valid": False}, {"v": 1, "t_s": 5.3, "yaw_deg": None},
              b"not json", {"v": 1, "yaw_deg": 33.0}):
        sub._on_message(c, None, Msg(d))
    got = sub.drain()
    assert [(s.kind, s.t, s.v) for s in got] == [(CAM, 5.0, (30.0,))]
    assert sub.rejected == 5 and sub.drain() == []


def test_parse_host():
    assert parse_host("broker") == ("broker", 1883)
    assert parse_host("10.0.0.5:1884") == ("10.0.0.5", 1884)
