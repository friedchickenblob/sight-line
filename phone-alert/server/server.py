"""Phone alert server: send a notification from this laptop to the Android app over the local network.

    python server.py                       # listens on 0.0.0.0:8765
    python server.py --token mysecret      # require a shared secret from phones and senders
    python server.py --mqtt localhost      # also forward MQTT messages on sightline/alert to the phone
    python server.py --dashboard           # also run the head-motion dashboard; it appears in the Dashboard tab

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
import shlex
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
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


class DashboardLink:
    """The head-motion dashboard (max/dashboard/app.py) as a tab on this server's page.

    It stays its own program on its own port (default 5000) and the Dashboard tab shows it in a frame.
    With --dashboard this server also starts it, passes its output through, and stops it on exit.
    """

    DEFAULT_SCRIPT = Path(__file__).resolve().parents[2] / "max" / "dashboard" / "app.py"

    def __init__(self, port: int = 5000, script: Optional[Path] = None, extra_args=(),
                 python: Optional[str] = None, lan: bool = False):
        self.port = port
        self.script = Path(script) if script else self.DEFAULT_SCRIPT
        self.extra_args = list(extra_args)
        self.python = python or sys.executable
        self.lan = lan
        self.proc: Optional[subprocess.Popen] = None
        self.log: deque = deque(maxlen=20)

    def command(self) -> list:
        host = "0.0.0.0" if self.lan else "127.0.0.1"
        return [self.python, str(self.script), *self.extra_args, "--host", host, "--port", str(self.port)]

    def start(self) -> None:
        if not self.script.is_file():
            raise FileNotFoundError(f"dashboard script not found: {self.script}")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(self.command(), cwd=str(self.script.parent.parent), stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, errors="replace", creationflags=flags)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for line in self.proc.stdout:
            line = line.rstrip()
            if line:
                self.log.append(line)
                print(f"[dashboard] {line}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def reachable(self) -> bool:
        try:
            socket.create_connection(("127.0.0.1", self.port), timeout=0.3).close()
            return True
        except OSError:
            return False

    def status(self) -> dict:
        exit_code = self.proc.poll() if self.proc else None
        try:
            shown = self.script.relative_to(self.DEFAULT_SCRIPT.parents[2]).as_posix()
        except ValueError:
            shown = str(self.script)
        return {
            "port": self.port,
            "managed": self.proc is not None,
            "reachable": self.reachable(),
            "exit_code": exit_code,
            "lan": self.lan,
            "script": str(self.script),
            "script_found": self.script.is_file(),
            "command": f"python {shown} --board synthetic     (run from the repo root, with the max/ virtual environment)",
            "log": list(self.log),
        }


PAGE = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SightLine control</title>
<script>
// Set the theme before the page paints: saved choice, otherwise the system setting.
(function () {
  var t = null;
  try { t = localStorage.getItem('sightline-theme'); } catch (e) {}
  if (t !== 'light' && t !== 'dark') t = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', t);
})();
</script>
<style>
 :root{color-scheme:light;--bg:#fff;--text:#1f2937;--muted:#64748b;--field:#fff;--line:#cbd5e1;--code:#f1f5f9;--pill:#e2e8f0;
  --alert:#fecaca;--warn:#fde68a;--ok:#15803d;--bad:#b91c1c;--btn:#14213d;--btn-text:#fff}
 :root[data-theme=dark]{color-scheme:dark;--bg:#111418;--text:#e6e8eb;--muted:#98a2b3;--field:#1a1e24;--line:#2b313a;--code:#1a1e24;--pill:#2b313a;
  --alert:#7f1d1d;--warn:#78350f;--ok:#3fbf74;--bad:#ef6b7d;--btn:#f2b705;--btn-text:#14213d}
 *{box-sizing:border-box}
 body{font:15px/1.4 system-ui,Segoe UI,sans-serif;margin:0;color:var(--text);background:var(--bg)}
 header{position:sticky;top:0;z-index:5;background:#14213d;color:#fff;display:flex;align-items:center;gap:1.2rem;padding:0 1rem;height:48px;border-bottom:3px solid #f2b705}
 header b{font-size:1rem;white-space:nowrap}
 nav{display:flex;gap:.2rem;height:100%}
 nav button{all:unset;cursor:pointer;padding:0 1rem;display:flex;align-items:center;gap:.4rem;color:#cbd5e1;font-weight:600;border-bottom:3px solid transparent;margin-bottom:-3px}
 nav button:hover{color:#fff}
 nav button[aria-selected=true]{color:#fff;border-bottom-color:#f2b705}
 nav button:focus-visible,#theme:focus-visible{outline:2px solid #f2b705;outline-offset:-2px}
 #theme{all:unset;cursor:pointer;margin-left:auto;width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#cbd5e1}
 #theme:hover{color:#fff;background:rgba(255,255,255,.12)} #theme:focus-visible{outline:2px solid #f2b705}
 #theme svg{width:20px;height:20px;fill:currentColor}
 :root[data-theme=light] .i-sun,:root[data-theme=dark] .i-moon{display:none}
 .led{width:.55rem;height:.55rem;border-radius:50%;background:#64748b;display:inline-block}
 .led.on{background:#22c55e}
 #notify,.msg{max-width:640px;margin:1.2rem auto;padding:0 1rem}
 #dash{height:calc(100vh - 51px)} #dash iframe{width:100%;height:100%;border:0;display:block;background:var(--bg)}
 h2{font-size:1rem;margin:1.4rem 0 .4rem}
 input,select,button.send{font:inherit;padding:.5rem;border:1px solid var(--line);border-radius:6px;width:100%;color:var(--text);background:var(--field)}
 button.send{background:var(--btn);color:var(--btn-text);border:0;cursor:pointer;margin-top:.5rem} button.send:active{opacity:.8}
 label{display:block;margin-top:.6rem;font-weight:600} code{background:var(--code);padding:.1rem .3rem;border-radius:4px}
 pre{background:var(--code);padding:.6rem;border-radius:6px;overflow:auto;max-height:14rem;font-size:.8rem}
 .pill{display:inline-block;padding:.1rem .5rem;border-radius:99px;font-size:.8rem;background:var(--pill)}
 .alert{background:var(--alert)}.warn{background:var(--warn)}.ok{color:var(--ok)}.bad{color:var(--bad)}
 ul{padding-left:1.1rem;margin:.3rem 0} #out{min-height:1.4em;margin-top:.4rem} [hidden]{display:none!important}
</style>
<header>
 <b>SightLine</b>
 <nav role="tablist" aria-label="Pages">
  <button role="tab" id="tab-notify" aria-selected="true" aria-controls="notify">Notifications</button>
  <button role="tab" id="tab-dash" aria-selected="false" aria-controls="dash">Dashboard <span class="led" id="led" title="dashboard status"></span></button>
 </nav>
 <button id="theme" type="button" aria-label="Switch to dark mode" title="Switch light / dark">
  <svg class="i-moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>
  <svg class="i-sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.5"/><path d="M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3M4.6 4.6l2.1 2.1M17.3 17.3l2.1 2.1M4.6 19.4l2.1-2.1M17.3 6.7l2.1-2.1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
 </button>
</header>

<section id="notify" role="tabpanel" aria-labelledby="tab-notify">
 <h1 style="font-size:1.3rem;margin:0 0 .2rem">Phone alerts</h1>
 <div>Phones connected: <b id="n">0</b></div>
 <h2>Connect the phone app to</h2><ul id="urls"></ul>
 <h2>Send a notification</h2>
 <label>Title<input id="t" value="Truck approaching"></label>
 <label>Message<input id="m" value="Look left"></label>
 <label>Level<select id="l"><option value="alert">alert (strong buzz)</option><option value="warn">warn</option><option value="info">info</option></select></label>
 <button class="send" id="send">Send to phone</button><div id="out"></div>
 <h2>Connected phones</h2><ul id="clients"></ul>
 <h2>Recent</h2><ul id="recent"></ul>
</section>

<section id="dash" role="tabpanel" aria-labelledby="tab-dash" hidden>
 <div id="dash-msg" class="msg" hidden></div>
 <iframe id="dash-frame" title="Head-motion dashboard" hidden></iframe>
</section>

<script>
const q = new URLSearchParams(location.search), token = q.get('token') || '';
const H = token ? {'X-Token': token} : {};
const $ = id => document.getElementById(id);

// ---- theme (light / dark): saved in this browser, and sent to the dashboard frame
function currentTheme() { return document.documentElement.getAttribute('data-theme'); }
function syncFrame() {
  const f = $('dash-frame');
  if (f.contentWindow) f.contentWindow.postMessage({type: 'sightline-theme', value: currentTheme()}, '*');
}
function applyTheme(t, save) {
  document.documentElement.setAttribute('data-theme', t);
  $('theme').setAttribute('aria-label', t === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
  if (save) { try { localStorage.setItem('sightline-theme', t); } catch (e) {} }
  syncFrame();
}
$('theme').onclick = () => applyTheme(currentTheme() === 'dark' ? 'light' : 'dark', true);
$('dash-frame').addEventListener('load', syncFrame);
applyTheme(currentTheme(), false);

// ---- tabs (the chosen tab survives a reload through the URL hash)
function show(name) {
  for (const t of ['notify', 'dash']) {
    $('tab-' + t).setAttribute('aria-selected', t === name);
    $(t).hidden = t !== name;
  }
  history.replaceState(null, '', name === 'dash' ? '#dashboard' : location.pathname + location.search);
  if (name === 'dash') pollDash();
}
$('tab-notify').onclick = () => show('notify');
$('tab-dash').onclick = () => show('dash');
if (location.hash === '#dashboard') show('dash');

// ---- notifications tab
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

// ---- dashboard tab: the head-motion dashboard runs on its own port and is shown in a frame
function dashMessage(html, log) {
  const m = $('dash-msg');
  m.innerHTML = html;
  if (log && log.length) { const p = document.createElement('pre'); p.textContent = log.join('\\n'); m.appendChild(p); }
  m.hidden = false;
  $('dash-frame').hidden = true;
}
async function pollDash() {
  let d;
  try { d = await (await fetch('/api/dashboard', {headers: H})).json(); } catch (e) { return; }
  $('led').className = 'led' + (d.reachable ? ' on' : '');
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);
  if (d.reachable && (local || d.lan)) {
    const base = location.protocol + '//' + location.hostname + ':' + d.port + '/';
    if ($('dash-frame').dataset.base !== base) {
      $('dash-frame').dataset.base = base;
      $('dash-frame').src = base + '?theme=' + currentTheme();
    }
    $('dash-frame').hidden = false;
    $('dash-msg').hidden = true;
  } else if (d.reachable) {
    dashMessage('<h2>Dashboard is running on the laptop</h2><p>It only accepts connections from the laptop itself. Open this page on the laptop at <code>http://localhost:' + location.port + '/#dashboard</code>, or restart the server with <code>--dashboard-lan</code> to open it from this device.</p>');
  } else if (d.exit_code !== null) {
    dashMessage('<h2>The dashboard stopped (exit code ' + d.exit_code + ')</h2><p>Last output:</p>', d.log);
  } else if (d.managed) {
    dashMessage('<h2>Starting the dashboard…</h2><p>The Muse can take up to a minute to connect.</p>', d.log);
  } else if (!d.script_found) {
    dashMessage('<h2>Dashboard code not found</h2><p>Expected <code>' + d.script + '</code>. The dashboard lives on the <code>sean/live-dashboard</code> branch until it is merged; point to it with <code>--dashboard-script</code>.</p>');
  } else {
    dashMessage('<h2>The dashboard is not running</h2><p>Restart this server with <code>--dashboard</code>, or start it yourself:</p><pre>' + d.command + '</pre>');
  }
}
poll(); setInterval(poll, 2000);
pollDash(); setInterval(pollDash, 3000);
</script>
"""


def create_app(hub: Optional[Hub] = None, token: Optional[str] = None, port: int = 8765,
               dashboard: Optional[DashboardLink] = None) -> Flask:
    hub = hub or Hub()
    dashboard = dashboard or DashboardLink()
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

    @app.get("/api/dashboard")
    def dashboard_status():
        if not authorised():
            abort(401)
        return jsonify(dashboard.status())

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
    p.add_argument("--dashboard", action="store_true",
                   help="also start the head-motion dashboard (max/dashboard/app.py) and show it in the Dashboard tab")
    p.add_argument("--dashboard-port", type=int, default=5000)
    p.add_argument("--dashboard-script", help="path to dashboard/app.py (default: max/dashboard/app.py in this repo)")
    p.add_argument("--dashboard-args", default="",
                   help='extra arguments for the dashboard; use the = form: --dashboard-args="--board synthetic" '
                        'or --dashboard-args="--source synthetic:joint"')
    p.add_argument("--dashboard-lan", action="store_true",
                   help="let other devices open the dashboard (it can start/stop recordings: only on a network you trust)")
    args = p.parse_args(argv)

    hub = Hub()
    dash = DashboardLink(args.dashboard_port, args.dashboard_script, shlex.split(args.dashboard_args), lan=args.dashboard_lan)
    app = create_app(hub, args.token, args.port, dash)
    if args.dashboard:
        try:
            dash.start()
            print(f"[dashboard] starting on port {args.dashboard_port} (tab: Dashboard)")
        except FileNotFoundError as e:
            print(f"[dashboard] {e}\n[dashboard] it lives on the sean/live-dashboard branch; use --dashboard-script to point to it")
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
    try:
        app.run(host=args.host, port=args.port, threaded=True, debug=False)
    finally:
        dash.stop()


if __name__ == "__main__":
    main()
