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
    def __init__(self, token=None, dashboard=None):
        self.port = free_port()
        self.hub = srv.Hub()
        self.app = srv.create_app(self.hub, token, self.port, dashboard)
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


# ---- dashboard tab -------------------------------------------------------------------------------------

FAKE_DASHBOARD = """
import argparse, socket, time
ap = argparse.ArgumentParser()
ap.add_argument("--host"); ap.add_argument("--port", type=int); ap.add_argument("--board", default="")
a = ap.parse_args()
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((a.host, a.port)); s.listen(5)
print("fake dashboard listening on", a.port, flush=True)
time.sleep(60)
"""


def fake_dashboard(tmp_path, body=FAKE_DASHBOARD):
    d = tmp_path / "max" / "dashboard"
    d.mkdir(parents=True)
    (d / "app.py").write_text(body)
    return d / "app.py"


def wait_for(pred, timeout=6):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_page_has_both_tabs(s):
    html = urllib.request.urlopen(s.base + "/").read().decode()
    assert 'id="tab-notify"' in html and 'id="tab-dash"' in html and "/api/dashboard" in html


def test_dashboard_missing_script_reported(s, tmp_path):
    link = srv.DashboardLink(port=free_port(), script=tmp_path / "nope" / "app.py")
    st = link.status()
    assert not st["script_found"] and not st["reachable"] and not st["managed"] and st["exit_code"] is None
    with pytest.raises(FileNotFoundError):
        link.start()


def test_api_dashboard_endpoint(tmp_path):
    link = srv.DashboardLink(port=free_port(), script=tmp_path / "nope" / "app.py")
    r = Running(dashboard=link)
    try:
        d = json.load(urllib.request.urlopen(r.base + "/api/dashboard"))
        assert d["port"] == link.port and d["reachable"] is False and d["script_found"] is False
    finally:
        r.close()


def test_dashboard_reachable_when_something_listens():
    srv_sock = socket.socket()
    srv_sock.bind(("127.0.0.1", 0))
    srv_sock.listen(1)
    try:
        link = srv.DashboardLink(port=srv_sock.getsockname()[1])
        assert link.status()["reachable"] is True
    finally:
        srv_sock.close()


def test_dashboard_started_and_stopped(tmp_path):
    port = free_port()
    link = srv.DashboardLink(port=port, script=fake_dashboard(tmp_path), extra_args=["--board", "synthetic"])
    link.start()
    try:
        assert wait_for(lambda: link.status()["reachable"])
        st = link.status()
        assert st["managed"] and st["exit_code"] is None
        assert wait_for(lambda: any("listening" in line for line in link.log))
        assert "127.0.0.1" in link.command() and "--port" in link.command() and "synthetic" in link.command()
    finally:
        link.stop()
    assert link.status()["exit_code"] is not None and not link.reachable()


def test_dashboard_crash_shows_exit_code_and_output(tmp_path):
    link = srv.DashboardLink(port=free_port(), script=fake_dashboard(tmp_path, 'print("boom: no Muse found")\nraise SystemExit(3)\n'))
    link.start()
    assert wait_for(lambda: link.status()["exit_code"] == 3)
    assert wait_for(lambda: any("boom" in line for line in link.log))
    assert link.status()["reachable"] is False


def test_dashboard_lan_flag_changes_bind_address(tmp_path):
    assert "0.0.0.0" in srv.DashboardLink(script=tmp_path / "a.py", lan=True).command()
    assert "127.0.0.1" in srv.DashboardLink(script=tmp_path / "a.py").command()


# ---- light / dark toggle -------------------------------------------------------------------------------

def test_page_has_theme_toggle_and_saves_choice(s):
    html = urllib.request.urlopen(s.base + "/").read().decode()
    assert 'id="theme"' in html and "sightline-theme" in html          # button + saved choice
    assert "prefers-color-scheme: dark" in html                         # default follows the system
    assert "data-theme=dark" in html                                    # dark palette exists
    assert "sightline-theme', value" in html                            # theme is sent to the dashboard frame


def test_dashboard_listens_for_theme_changes():
    from pathlib import Path
    dash = srv.DashboardLink.DEFAULT_SCRIPT.parent
    js = (dash / "static" / "theme.js").read_text(encoding="utf-8")
    css = (dash / "static" / "style.css").read_text(encoding="utf-8")
    html = (dash / "templates" / "index.html").read_text(encoding="utf-8")
    assert "sightline-theme" in js and "window.parent" in js
    assert '[data-theme="dark"]' in css and '[data-theme="light"]' in css
    assert "theme.js" in html


# ------------------------------------------------------------------ continuous buzz
def post_buzz(s, body):
    req = urllib.request.Request(s.base + "/buzz", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def read_until(resp, event, timeout=5):
    cur, deadline = {}, time.time() + timeout
    while time.time() < deadline:
        line = resp.readline().decode().rstrip("\n")
        if line == "":
            if cur.get("event") == event:
                return cur
            cur = {}
        elif ":" in line:
            k, _, v = line.partition(":")
            cur[k] = v.strip()
    raise AssertionError(f"no {event} event")


def test_buzz_on_and_off_reach_the_phone_without_ids(s):
    resp = open_events(s)
    wait_clients(s, 1)
    code, body = post_buzz(s, {"v": 1, "on": True, "hold_s": 1.5, "title": "Look at the toy car", "message": "left"})
    assert code == 200 and body["delivered_to"] == 1
    ev = read_until(resp, "buzz")
    d = json.loads(ev["data"])
    assert d["on"] is True and d["hold_s"] == 1.5 and d["title"] == "Look at the toy car" and "id" not in ev
    assert s.hub.buzzing()
    post_buzz(s, {"v": 1, "on": False})
    assert json.loads(read_until(resp, "buzz")["data"])["on"] is False
    assert not s.hub.buzzing()
    assert len(s.hub.history) == 0                     # never stored for replay


def test_phone_joining_mid_buzz_gets_the_rest_of_it(s):
    post_buzz(s, {"v": 1, "on": True, "hold_s": 5})
    resp = open_events(s)
    d = json.loads(read_until(resp, "buzz")["data"])
    assert d["on"] is True and 0 < d["hold_s"] <= 5


def test_expired_buzz_not_sent_to_new_phone(s):
    post_buzz(s, {"v": 1, "on": True, "hold_s": 0.1})
    time.sleep(0.2)
    assert not s.hub.buzzing()
    resp = open_events(s)
    wait_clients(s, 1)
    post_buzz(s, {"v": 1, "on": False})
    assert json.loads(read_until(resp, "buzz")["data"])["on"] is False   # the first buzz it sees is the off


@pytest.mark.parametrize("body", [{"v": 1}, {"v": 1, "on": "yes"}, {"v": 1, "on": True, "hold_s": 0},
                                  {"v": 1, "on": True, "hold_s": 60}, {"v": 2, "on": True}])
def test_bad_buzz_rejected(s, body):
    code, out = post_buzz(s, body)
    assert code == 400 and not out["ok"]
