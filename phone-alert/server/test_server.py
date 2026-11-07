"""Tests: run the real server on a free port and read /events like the phone does."""
import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest
from werkzeug.serving import make_server

import server as srv


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Running:
    def __init__(self, token=None):
        self.port = free_port()
        self.hub = srv.Hub()
        self.app = srv.create_app(self.hub, token, self.port)
        self.httpd = make_server("127.0.0.1", self.port, self.app, threaded=True)
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def post(self, body, token=None, raw=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Token"] = token
        req = urllib.request.Request(self.base + "/notify", data=raw or json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def s():
    r = Running()
    yield r
    r.close()


def read_events(resp, want, timeout=5):
    """Collect SSE events from an open response until `want` alert events have arrived."""
    out, cur = [], {}
    deadline = time.time() + timeout
    while time.time() < deadline and sum(1 for e in out if e["event"] == "alert") < want:
        line = resp.readline().decode().rstrip("\n")
        if line == "":
            if cur:
                out.append(cur)
                cur = {}
        elif ":" in line:
            k, _, v = line.partition(":")
            cur[k] = v.strip()
    return out


def open_events(s, device="test", headers=None, query=""):
    req = urllib.request.Request(f"{s.base}/events?device={device}{query}", headers=headers or {})
    return urllib.request.urlopen(req, timeout=8)


def wait_clients(s, n, timeout=3):
    end = time.time() + timeout
    while time.time() < end and len(s.hub.clients) < n:
        time.sleep(0.02)
    assert len(s.hub.clients) == n


def test_health_identifies_service(s):
    d = json.load(urllib.request.urlopen(s.base + "/health"))
    assert d["service"] == srv.SERVICE and d["v"] == 1


def test_notify_reaches_connected_phone(s):
    resp = open_events(s)
    wait_clients(s, 1)
    code, body = s.post({"v": 1, "title": "Truck", "message": "Look left", "level": "alert"})
    assert code == 200 and body["delivered_to"] == 1
    evs = read_events(resp, 1)
    alert = [e for e in evs if e["event"] == "alert"][0]
    data = json.loads(alert["data"])
    assert (data["title"], data["message"], data["level"], data["v"]) == ("Truck", "Look left", "alert", 1)
    assert evs[0]["event"] == "hello"


def test_two_phones_both_get_it(s):
    a, b = open_events(s, "a"), open_events(s, "b")
    wait_clients(s, 2)
    assert s.post({"title": "x"})[1]["delivered_to"] == 2
    assert read_events(a, 1)[-1]["event"] == "alert" and read_events(b, 1)[-1]["event"] == "alert"


def test_no_phone_means_zero_delivered(s):
    assert s.post({"title": "x"}) [1]["delivered_to"] == 0


def test_reconnect_replays_missed_alert(s):
    first = open_events(s)
    wait_clients(s, 1)
    s.post({"title": "one"})
    ev = [e for e in read_events(first, 1) if e["event"] == "alert"][0]
    first.close()
    time.sleep(0.2)
    s.post({"title": "missed"})
    again = open_events(s, headers={"Last-Event-ID": ev["id"]})
    titles = [json.loads(e["data"])["title"] for e in read_events(again, 1) if e["event"] == "alert"]
    assert titles == ["missed"]


def test_expired_ttl_not_replayed(s):
    s.post({"title": "stale", "ttl_s": 0.01})
    time.sleep(0.1)
    resp = open_events(s, headers={"Last-Event-ID": "0"})
    wait_clients(s, 1)
    s.post({"title": "fresh"})
    titles = [json.loads(e["data"])["title"] for e in read_events(resp, 1) if e["event"] == "alert"]
    assert titles == ["fresh"]


@pytest.mark.parametrize("body,frag", [
    ({"level": "loud"}, "level"),
    ({"v": 2}, "version"),
    ({"ttl_s": "abc"}, "ttl_s"),
])
def test_bad_payloads_rejected(s, body, frag):
    code, d = s.post(body)
    assert code == 400 and frag in d["error"]


def test_bad_json_rejected(s):
    code, d = s.post(None, raw=b"[1,2]")
    assert code == 400


def test_token_required_when_set():
    r = Running(token="sesame")
    try:
        assert r.post({"title": "x"})[0] == 401
        assert r.post({"title": "x"}, token="sesame")[0] == 200
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(r.base + "/events")
        assert e.value.code == 401
        assert urllib.request.urlopen(r.base + "/events?token=sesame", timeout=3).status == 200
    finally:
        r.close()


def test_stuck_phone_does_not_block_others(s):
    stuck = srv.Client("stuck", "1.2.3.4")
    s.hub.clients.append(stuck)           # never read from
    for i in range(srv.CLIENT_QUEUE + 20):
        s.hub.broadcast({"title": str(i), "message": "", "level": "info", "ttl_s": None})
    assert stuck.q.full()


def test_mqtt_payload_uses_same_validation():
    hub = srv.Hub()
    msg = srv.clean_message({"v": 1, "title": "from mqtt", "level": "warn"})
    assert hub.broadcast(msg, source="mqtt")["source"] == "mqtt"
    with pytest.raises(ValueError):
        srv.clean_message(json.loads('{"level": "nope"}'))


def test_state_lists_phone_and_recent(s):
    resp = open_events(s, "pixel")
    wait_clients(s, 1)
    s.post({"title": "hi"})
    d = json.load(urllib.request.urlopen(s.base + "/api/state"))
    assert d["clients"][0]["name"] == "pixel" and d["recent"][0]["title"] == "hi"
    resp.close()
