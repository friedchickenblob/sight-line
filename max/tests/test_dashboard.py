"""Live dashboard: recording buttons, live fields, and the event stream."""
import json

import pytest

pytest.importorskip("flask")

from dashboard.app import Hub, create_app                     # noqa: E402
from headmotion import synth                                  # noqa: E402
from headmotion.sources import CAM, DROP, GYRO, MARK, RESET, Replay, read_csv  # noqa: E402
from headmotion.yaw import HeadYawEstimator, YawPipeline       # noqa: E402


def _hub(tmp_path, samples=None):
    samples = samples if samples is not None else synth.scene("joint")
    return Hub(Replay(samples), YawPipeline(HeadYawEstimator(), retry_bias=True), tmp_path, source_name="test")


def test_hub_runs_a_whole_source_and_shows_every_kind(tmp_path):
    s = synth.scene("joint")[:20000]
    hub = _hub(tmp_path, s)
    hub._run()
    snap = hub.snapshot()
    assert hub.ended and snap["ended"]
    assert snap["counts"][GYRO] == sum(x.kind == GYRO for x in s)
    assert snap["latest"][CAM][0] == CAM and len(snap["latest"][GYRO]) == 5     # kind, t, v0, v1, v2
    assert snap["yaw"]["yaw_deg"] is not None and snap["bias"] is not None
    assert json.dumps(snap)                                    # what /stream sends must serialise


def test_recording_writes_exactly_what_streamed(tmp_path):
    s = synth.scene("bluetooth")[:3000]                       # has DROP rows too
    hub = _hub(tmp_path, s)
    hub.start_recording("b1 test/../x")                       # name is sanitised into raw_dir
    hub._run()
    state = hub.stop_recording()
    path = tmp_path / "b1_test_x.csv"
    assert state["name"] == path.name and path.exists()
    back = read_csv(path)
    assert back == s and state["rows"] == len(s)
    assert any(x.kind == DROP for x in back)


def test_start_twice_and_stop_without_start_are_refused(tmp_path):
    hub = _hub(tmp_path, [])
    with pytest.raises(RuntimeError, match="not recording"):
        hub.stop_recording()
    hub.start_recording("a")
    with pytest.raises(RuntimeError, match="already recording"):
        hub.start_recording("b")
    hub.stop_recording()
    with pytest.raises(RuntimeError, match="already exists"):
        hub.start_recording("a")


def test_mark_and_rezero_become_rows(tmp_path):
    s = synth.scene("axis")
    hub = _hub(tmp_path, s)
    hub.rezero()
    hub.step([])                                               # before the bias window: ignored, with a note
    assert RESET not in hub.counts and "re-zero ignored" in hub.log[-1][2]
    hub.start_recording("marks")
    hub.step(s[:2000])                                         # past the 5 s bias window
    hub.mark(); hub.mark(); hub.rezero()
    hub.step([])
    hub.stop_recording()
    rows = read_csv(tmp_path / "marks.csv")
    assert [x.v for x in rows if x.kind == MARK] == [(0.0,), (1.0,)]
    assert [x.v for x in rows if x.kind == RESET] == [(0.0,)]


def test_routes(tmp_path):
    hub = _hub(tmp_path, synth.scene("axis")[:3000])
    hub.step(hub.src._samples[:2000])
    c = create_app(hub).test_client()
    assert b"SightLine" in c.get("/").data
    assert c.post("/api/record/start", json={"name": "r1"}).get_json()["recording"]["on"]
    r = c.post("/api/record/start", json={"name": "r2"})
    assert r.status_code == 409 and "already recording" in r.get_json()["error"]
    assert c.post("/api/mark").get_json()["ok"]
    hub.step([])
    assert c.post("/api/record/stop").get_json()["recording"]["name"] == "r1.csv"
    assert c.post("/api/record/stop").status_code == 409
    st = c.get("/api/state").get_json()
    assert st["counts"][MARK] == 1 and st["recording"] == {"on": False}


def test_stream_sends_rows_as_server_sent_events(tmp_path):
    hub = _hub(tmp_path, synth.scene("axis")[:1400])          # past the 5 s bias window
    hub.step(hub.src._samples)
    resp = create_app(hub).test_client().get("/stream", buffered=False)
    assert resp.mimetype == "text/event-stream"
    first = next(resp.response).decode()
    resp.close()
    assert first.startswith("data: ")
    snap = json.loads(first[len("data: "):])
    assert len(snap["rows"]) == min(hub.seq, 2000) and snap["rows"][0][0] in (GYRO, "ACC")
    assert {"health", "yaw", "latest", "counts", "recording", "log"} <= set(snap)
    assert snap["yaws"] and len(snap["yaws"][0]) == 2          # yaw history for the chart, from the backlog too


class FakePhone:
    """Stands in for stream_data's PhoneStream (no aiortc needed here)."""
    url = "https://192.168.1.23:8443"

    def __init__(self, fail=False):
        self.fail = fail

    def status(self):
        if self.fail:
            raise TimeoutError("server busy")
        return {"model": "null", "sessions": [{"id": 1, "state": "connected", "frames_in": 30, "frames_done": 30,
                                               "frames_dropped": 0, "infer_s": 0.002, "elapsed_s": 1.0,
                                               "latest": {"mean_brightness": 120.0}}]}

    def mjpeg(self, session_id):
        if session_id != 1:
            return None
        return iter([b"--frame\r\nContent-Type: image/jpeg\r\n\r\nJPEG\r\n"])


def test_phone_card_is_off_without_phone(tmp_path):
    c = create_app(_hub(tmp_path, [])).test_client()
    assert c.get("/api/phone").get_json() == {"enabled": False}
    assert c.get("/phone/video/1.mjpg").status_code == 404
    assert b'id="phone-card"' in c.get("/").data


def test_phone_status_and_video_come_through_the_dashboard(tmp_path):
    c = create_app(_hub(tmp_path, []), FakePhone()).test_client()
    p = c.get("/api/phone").get_json()
    assert p["enabled"] and p["url"] == FakePhone.url and p["error"] is None
    assert p["model"] == "null" and p["sessions"][0]["frames_in"] == 30
    r = c.get("/phone/video/1.mjpg")
    assert r.status_code == 200 and r.content_type.startswith("multipart/x-mixed-replace")
    assert r.data.startswith(b"--frame")
    assert c.get("/phone/video/2.mjpg").status_code == 404


def test_phone_errors_show_on_the_page_not_as_a_500(tmp_path):
    p = create_app(_hub(tmp_path, []), FakePhone(fail=True)).test_client().get("/api/phone").get_json()
    assert p["enabled"] and "TimeoutError" in p["error"] and p["sessions"] == []


class FakeMuse(Replay):
    """A Replay with LiveMuse's search interface."""

    def __init__(self, found=True):
        super().__init__([])
        from collections import deque
        self.connected, self.searching, self.found, self.events = False, False, found, deque()

    def search(self):
        self.connected, self.searching = self.found, False
        self.events.append("Muse connected" if self.found else "could not start the Muse")
        return self.found


def test_muse_search_button_without_a_muse_source_is_refused(tmp_path):
    c = create_app(_hub(tmp_path, [])).test_client()
    assert c.get("/api/state").get_json()["muse"]["available"] is False
    r = c.post("/api/muse/search")
    assert r.status_code == 409 and not r.get_json()["ok"]


@pytest.mark.parametrize("found", [True, False])
def test_muse_search_button_runs_a_search_and_logs_it(tmp_path, found):
    import time
    src = FakeMuse(found)
    hub = Hub(src, YawPipeline(HeadYawEstimator(), retry_bias=True), tmp_path, source_name="muse")
    c = create_app(hub).test_client()
    assert c.get("/api/state").get_json()["muse"] == dict(available=True, connected=False, searching=False)
    assert c.post("/api/muse/search").get_json()["ok"]
    t_end = time.monotonic() + 2
    while src.searching and time.monotonic() < t_end:
        time.sleep(0.01)
    hub.step([])
    snap = hub.snapshot()
    assert snap["muse"]["connected"] is found and not snap["muse"]["searching"]
    assert any(("Muse connected" if found else "could not start") in text for _, text in snap["log"])
