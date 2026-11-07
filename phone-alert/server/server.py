"""Phone alert server: send a notification from this laptop to the Android app over the local network.

    python server.py                       # listens on 0.0.0.0:8765
    python server.py --token mysecret      # require a shared secret from phones and senders
    python server.py --mqtt localhost      # also forward MQTT messages on sightline/alert to the phone

Send an alert (from anywhere that can reach the laptop):

    curl -X POST http://<laptop-ip>:8765/notify -H "Content-Type: application/json" ^
         -d "{\"v\":1,\"title\":\"Truck approaching\",\"message\":\"Look left\",\"level\":\"alert\"}"

or open http://<laptop-ip>:8765/ in a browser and use the form.

How it reaches the phone: the Android app keeps one HTTP connection open to /events
(Server-Sent Events). No Google services, no internet and no router are needed, only
that the phone can open a TCP connection to this laptop. Works on a Windows Mobile
Hotspot (the laptop is usually 192.168.137.1) and on a phone hotspot (laptop joins it).

Message shape follows the repo convention (CLAUDE.md): JSON, snake_case, "v": 1.
    title      str, default "Alert"
    message    str, default ""
    level      "info" | "warn" | "alert"   (alert = strong vibration, heads-up notification)
    ttl_s      float, optional: phones that reconnect later than this do not get it replayed
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import secrets
import socket
import threading
import time
from collections import deque
from typing import Optional

from flask import Flask, Response, abort, jsonify, request

SERVICE = "sightline-phone-alert"
SCHEMA_VERSION = 1
LEVELS = ("info", "warn", "alert")
HEARTBEAT_S = 5.0          # the app times out after ~3x this
REPLAY_WINDOW_S = 30.0     # on reconnect, resend missed alerts no older than this
HISTORY = 200
CLIENT_QUEUE = 100


def clean_message(data: dict) -> dict:
    """Validate a /notify or MQTT payload into the fields the phone shows. Raises ValueError."""
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    v = data.get("v", SCHEMA_VERSION)
    if v != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema version {v!r} (this server speaks v{SCHEMA_VERSION})")
    level = str(data.get("level", "alert")).lower()
    if level not in LEVELS:
        raise ValueError(f"level must be one of {', '.join(LEVELS)}")
    ttl = data.get("ttl_s")
    if ttl is not None:
        try:
            ttl = float(ttl)
        except (TypeError, ValueError):
            raise ValueError("ttl_s must be a number") from None
    return {
        "title": str(data.get("title") or "Alert")[:120],
        "message": str(data.get("message") or "")[:500],
        "level": level,
        "ttl_s": ttl,
    }


class Client:
    def __init__(self, name: str, ip: str):
        self.name, self.ip = name, ip
        self.q: queue.Queue = queue.Queue(maxsize=CLIENT_QUEUE)
        self.connected_at = self.last_seen = time.time()


class Hub:
    """Fans every alert out to every connected phone, and remembers the last few for replay."""

    def __init__(self):
        self.lock = threading.Lock()
        self.clients: list[Client] = []
        self.history: deque = deque(maxlen=HISTORY)
        self.next_id = 1
        self.boot = secrets.token_hex(4)   # changes on every server start so phones reset their last-seen id

    def add_client(self, name: str, ip: str, last_id: Optional[int]) -> Client:
        c = Client(name, ip)
        with self.lock:
            self.clients.append(c)
            now = time.time()
            if last_id is not None:
                for ev in self.history:
                    ttl = ev["ttl_s"] if ev["ttl_s"] is not None else REPLAY_WINDOW_S
                    if ev["id"] > last_id and now - ev["sent_at"] <= min(ttl, REPLAY_WINDOW_S):
                        c.q.put_nowait(ev)
        return c

    def remove_client(self, c: Client) -> None:
        with self.lock:
            if c in self.clients:
                self.clients.remove(c)

    def broadcast(self, msg: dict, source: str = "http") -> dict:
        with self.lock:
            ev = {"id": self.next_id, "sent_at": time.time(), "source": source, **msg}
            self.next_id += 1
            self.history.append(ev)
            delivered = 0
            for c in self.clients:
                try:
                    c.q.put_nowait(ev)
                    delivered += 1
                except queue.Full:
                    pass                       # a stuck phone must not block the others
            ev = {**ev, "delivered_to": delivered}
        return ev

    def snapshot(self) -> dict:
        now = time.time()
        with self.lock:
            return {
                "clients": [{"name": c.name, "ip": c.ip, "connected_s": round(now - c.connected_at, 1),
                             "last_seen_s": round(now - c.last_seen, 1)} for c in self.clients],
                "recent": [{k: ev[k] for k in ("id", "sent_at", "title", "message", "level", "source")}
                           for ev in list(self.history)[-15:]][::-1],
            }


def lan_addresses() -> list[str]:
    """IPv4 addresses of this machine that a phone could plausibly reach."""
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")) and ip not in found:
                found.append(ip)
    except OSError:
        pass
    try:                                       # the address Windows would use to reach the outside
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("203.0.113.1", 9))
        ip = s.getsockname()[0]
        s.close()
        if ip not in found and not ip.startswith("127."):
            found.insert(0, ip)
    except OSError:
        pass
    return found


PAGE = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Phone alert server</title>
<style>
 body{font:15px/1.4 system-ui,Segoe UI,sans-serif;max-width:640px;margin:1.5rem auto;padding:0 1rem;color:#1f2937}
 h1{font-size:1.3rem;margin:0 0 .2rem} h2{font-size:1rem;margin:1.4rem 0 .4rem}
 input,select,textarea,button{font:inherit;padding:.5rem;border:1px solid #cbd5e1;border-radius:6px;width:100%;box-sizing:border-box}
 button{background:#14213d;color:#fff;border:0;cursor:pointer;margin-top:.5rem} button:active{opacity:.8}
 label{display:block;margin-top:.6rem;font-weight:600} code{background:#f1f5f9;padding:.1rem .3rem;border-radius:4px}
 .pill{display:inline-block;padding:.1rem .5rem;border-radius:99px;font-size:.8rem;background:#e2e8f0}
 .alert{background:#fecaca}.warn{background:#fde68a}.ok{color:#15803d}.bad{color:#b91c1c}
 ul{padding-left:1.1rem;margin:.3rem 0} #out{min-height:1.4em;margin-top:.4rem}
</style>
<h1>Phone alert server</h1>
<div>Phones connected: <b id="n">0</b></div>
<h2>Connect the phone app to</h2><ul id="urls"></ul>
<h2>Send a notification</h2>
<label>Title<input id="t" value="Truck approaching"></label>
<label>Message<input id="m" value="Look left"></label>
<label>Level<select id="l"><option value="alert">alert (strong buzz)</option><option value="warn">warn</option><option value="info">info</option></select></label>
<button id="send">Send to phone</button><div id="out"></div>
<h2>Connected phones</h2><ul id="clients"></ul>
<h2>Recent</h2><ul id="recent"></ul>
<script>
const q = new URLSearchParams(location.search), token = q.get('token') || '';
const H = token ? {'X-Token': token} : {};
const $ = id => document.getElementById(id);
fetch('/api/urls', {headers: H}).then(r => r.json()).then(d => {
  $('urls').innerHTML = d.urls.map(u => '<li><code>' + u + '</code></li>').join('') || '<li>no network address found</li>';
});
async function poll(){
  try {
    const d = await (await fetch('/api/state', {headers: H})).json();
    $('n').textContent = d.clients.length;
    $('clients').innerHTML = d.clients.map(c => '<li>' + c.name + ' (' + c.ip + ') connected ' + Math.round(c.connected_s) + ' s</li>').join('') || '<li>none yet</li>';
    $('recent').innerHTML = d.recent.map(r => '<li><span class="pill ' + r.level + '">' + r.level + '</span> ' + r.title + (r.message ? ': ' + r.message : '') + '</li>').join('') || '<li>nothing sent yet</li>';
  } catch (e) {}
}
$('send').onclick = async () => {
  const body = {v: 1, title: $('t').value, message: $('m').value, level: $('l').value};
  const r = await fetch('/notify', {method: 'POST', headers: {...H, 'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const d = await r.json();
  $('out').innerHTML = r.ok ? '<span class="ok">Sent to ' + d.delivered_to + ' phone(s)</span>' : '<span class="bad">' + (d.error || r.status) + '</span>';
  poll();
};
poll(); setInterval(poll, 2000);
</script>
"""


def create_app(hub: Optional[Hub] = None, token: Optional[str] = None, port: int = 8765) -> Flask:
    hub = hub or Hub()
    app = Flask(__name__)
    app.config["HUB"] = hub

    def authorised() -> bool:
        if not token:
            return True
        given = request.headers.get("X-Token") or request.args.get("token") or ""
        return given == token

    @app.get("/")
    def index():
        return Response(PAGE, mimetype="text/html")

    @app.get("/health")
    def health():
        return jsonify(service=SERVICE, v=SCHEMA_VERSION, clients=len(hub.clients),
                       auth_required=bool(token))

    @app.get("/api/urls")
    def urls():
        return jsonify(urls=[f"{ip}:{port}" for ip in lan_addresses()])

    @app.get("/api/state")
    def state():
        if not authorised():
            abort(401)
        return jsonify(hub.snapshot())

    @app.post("/notify")
    def notify():
        if not authorised():
            return jsonify(ok=False, error="bad or missing token"), 401
        data = request.get_json(silent=True)
        if data is None and request.form:
            data = request.form.to_dict()
        try:
            msg = clean_message(data if data is not None else {})
        except ValueError as e:
            return jsonify(ok=False, error=str(e)), 400
        ev = hub.broadcast(msg, source="http")
        return jsonify(ok=True, id=ev["id"], delivered_to=ev["delivered_to"])

    @app.get("/events")
    def events():
        if not authorised():
            abort(401)
        name = (request.args.get("device") or "phone")[:40]
        try:
            last_id = int(request.headers.get("Last-Event-ID") or request.args.get("last_id") or "")
        except ValueError:
            last_id = None
        client = hub.add_client(name, request.remote_addr or "?", last_id)

        def stream():
            try:
                yield "retry: 2000\n"
                yield f"event: hello\ndata: {json.dumps({'v': SCHEMA_VERSION, 'service': SERVICE, 'boot': hub.boot})}\n\n"
                while True:
                    try:
                        ev = client.q.get(timeout=HEARTBEAT_S)
                    except queue.Empty:
                        client.last_seen = time.time()
                        yield f"event: ping\ndata: {int(time.time())}\n\n"
                        continue
                    client.last_seen = time.time()
                    payload = {"v": SCHEMA_VERSION, "id": ev["id"], "title": ev["title"], "message": ev["message"],
                               "level": ev["level"], "sent_at": ev["sent_at"]}
                    yield f"id: {ev['id']}\nevent: alert\ndata: {json.dumps(payload)}\n\n"
            finally:
                hub.remove_client(client)

        return Response(stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

    return app


def start_mqtt_bridge(hub: Hub, host_spec: str, topic: str):
    """Forward JSON messages on an MQTT topic to the phones (same payload as POST /notify)."""
    import paho.mqtt.client as mqtt

    host, _, port = host_spec.partition(":")
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def on_connect(client, userdata, flags, reason_code, properties=None):
        client.subscribe(topic, qos=0)
        print(f"[mqtt] subscribed to {topic} on {host_spec}")

    def on_message(client, userdata, m):
        try:
            hub.broadcast(clean_message(json.loads(m.payload)), source="mqtt")
        except ValueError as e:                # includes bad JSON (JSONDecodeError is a ValueError)
            print(f"[mqtt] ignored message on {m.topic}: {e}")

    c.on_connect, c.on_message = on_connect, on_message
    c.connect_async(host or "localhost", int(port) if port else 1883)
    c.loop_start()
    return c


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="0.0.0.0", help="address to listen on (default: all interfaces)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--token", default=os.environ.get("PHONE_ALERT_TOKEN"),
                   help="shared secret; phones and senders must send it (or set PHONE_ALERT_TOKEN)")
    p.add_argument("--mqtt", metavar="HOST[:PORT]", help="also forward MQTT alerts to the phones")
    p.add_argument("--mqtt-topic", default="sightline/alert", help="topic to forward (default: sightline/alert)")
    args = p.parse_args(argv)

    hub = Hub()
    app = create_app(hub, args.token, args.port)
    if args.mqtt:
        start_mqtt_bridge(hub, args.mqtt, args.mqtt_topic)

    print(f"\nPhone alert server on port {args.port}" + ("  (token required)" if args.token else ""))
    print("Put one of these in the phone app:")
    for ip in lan_addresses() or ["<no network address found>"]:
        print(f"    {ip}:{args.port}")
    print("Windows Mobile Hotspot is usually 192.168.137.1. Test page: "
          f"http://localhost:{args.port}/\n")

    import logging
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.run(host=args.host, port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
