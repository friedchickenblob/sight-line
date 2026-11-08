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

Continuous buzz (the vision server's "worker isn't facing the hazard" signal):

    POST /buzz  {"v": 1, "on": true, "hold_s": 1.5, "title": "Look at the toy car", "message": "..."}
    POST /buzz  {"v": 1, "on": false}

"on" starts the app's sound + vibration and must be repeated (sooner than hold_s) to keep it going:
the app stops by itself once hold_s passes without one. "off" stops it at once. Buzz messages go
straight to the connected phones and are never stored or replayed (a late buzz is a wrong buzz).

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


BUZZ_MAX_HOLD_S = 10.0


def clean_buzz(data: dict) -> dict:
    """Validate a /buzz payload. Raises ValueError."""
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    v = data.get("v", SCHEMA_VERSION)
    if v != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema version {v!r} (this server speaks v{SCHEMA_VERSION})")
    if not isinstance(data.get("on"), bool):
        raise ValueError('"on" must be true or false')
    try:
        hold = float(data.get("hold_s", 1.5))
    except (TypeError, ValueError):
        raise ValueError("hold_s must be a number") from None
    if not 0 < hold <= BUZZ_MAX_HOLD_S:
        raise ValueError(f"hold_s must be in (0, {BUZZ_MAX_HOLD_S:g}]")
    return {"on": data["on"], "hold_s": hold,
            "title": str(data.get("title") or "Look at the hazard")[:120],
            "message": str(data.get("message") or "")[:500]}


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
        self.buzz: Optional[dict] = None   # last buzz state; "on" counts only until its hold time runs out

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
            b = self.buzz                      # a phone joining mid-buzz starts buzzing for what's left of it
            if b and b["on"] and now < b["sent_at"] + b["hold_s"]:
                c.q.put_nowait({**b, "hold_s": round(b["sent_at"] + b["hold_s"] - now, 3)})
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

    def set_buzz(self, msg: dict) -> int:
        """Push a buzz on/off straight to every connected phone (not stored for replay)."""
        with self.lock:
            ev = {"kind": "buzz", "sent_at": time.time(), **msg}
            self.buzz = ev
            delivered = 0
            for c in self.clients:
                try:
                    c.q.put_nowait(ev)
                    delivered += 1
                except queue.Full:
                    pass
        return delivered

    def buzzing(self) -> bool:
        b = self.buzz
        return bool(b and b["on"] and time.time() < b["sent_at"] + b["hold_s"])

    def snapshot(self) -> dict:
        now = time.time()
        with self.lock:
            return {
                "buzzing": self.buzzing(),
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
<html lang="en">
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SightLine control</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Oswald:wght@500;600&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
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
 /* Colours, fonts and the hazard tape come from the SightLine pitch deck */
 :root{color-scheme:light;
  --tape-y:#FFCC4D;--tape-d:#292F33;--yellow:#FACC15;--amber:#FFBF00;--cream:#FFF2CC;--green:#4F7E52;--ink:#09090B;
  --bg:#F4F4F5;--card:#FFFFFF;--card2:#FAFAFA;--text:#18181B;--muted:#52525B;--line:#E4E4E7;--field:#FFFFFF;
  --head:#27272A;--focus:#FFBF00;
  --alert-bg:#FEE2E2;--alert-fg:#991B1B;--warn-bg:#FEF08A;--warn-fg:#854D0E;--ok-bg:#DCFCE7;--ok-fg:#166534;--info-bg:#E4E4E7;--info-fg:#27272A;
  --display:'Oswald','Arial Narrow',Impact,sans-serif;--heading:'Space Grotesk','Segoe UI',system-ui,sans-serif;--body:'Inter','Segoe UI',system-ui,sans-serif}
 :root[data-theme=dark]{color-scheme:dark;
  --bg:#393939;--card:#27272A;--card2:#1F1F22;--text:#FAFAFA;--muted:#A1A1AA;--line:#52525B;--field:#18181B;--head:#09090B;
  --alert-bg:#FEE2E2;--alert-fg:#991B1B;--warn-bg:#FEF08A;--warn-fg:#854D0E;--ok-bg:#DCFCE7;--ok-fg:#166534;--info-bg:#3F3F46;--info-fg:#E4E4E7}
 *{box-sizing:border-box}
 html,body{margin:0}
 body{font:15px/1.5 var(--body);color:var(--text);background:var(--bg)}
 .tape{height:10px;background:repeating-linear-gradient(-45deg,var(--tape-y) 0 14px,var(--tape-d) 14px 28px)}
 header{position:sticky;top:0;z-index:5;background:var(--head);color:#fff}
 .bar{display:flex;align-items:center;gap:1.4rem;padding:0 1.2rem;height:58px}
 .brand{display:flex;align-items:baseline;gap:.6rem;white-space:nowrap}
 .brand .mark{font:600 1.55rem/1 var(--display);letter-spacing:.06em;color:var(--amber)}
 .brand .sub{font:600 .78rem var(--heading);letter-spacing:.12em;text-transform:uppercase;color:var(--cream);opacity:.8}
 nav{display:flex;gap:.35rem}
 nav button{all:unset;cursor:pointer;padding:.45rem .9rem;border-radius:8px;display:flex;align-items:center;gap:.5rem;
  font:600 .92rem var(--heading);color:#D4D4D8}
 nav button:hover{background:rgba(255,255,255,.08);color:#fff}
 nav button[aria-selected=true]{background:var(--yellow);color:var(--ink)}
 nav button:focus-visible,#theme:focus-visible,.btn:focus-visible,.chip:focus-visible,.stat:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
 #theme{all:unset;cursor:pointer;margin-left:auto;width:38px;height:38px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#D4D4D8}
 #theme:hover{color:#fff;background:rgba(255,255,255,.1)}
 #theme svg{width:20px;height:20px;fill:currentColor}
 :root[data-theme=light] .i-sun,:root[data-theme=dark] .i-moon{display:none}
 .led{width:.6rem;height:.6rem;border-radius:50%;background:#71717A;display:inline-block;box-shadow:0 0 0 2px rgba(0,0,0,.25)}
 .led.on{background:#22C55E}
 main{max-width:1180px;margin:0 auto;padding:1.4rem 1.2rem 2.4rem}
 h1,h2,h3{font-family:var(--heading);margin:0}
 .page-title{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:.6rem 1rem;margin-bottom:1.1rem}
 .page-title h1{font-size:1.8rem;line-height:1.15}
 :root[data-theme=dark] .page-title h1{color:var(--cream)}
 .page-title p{margin:.2rem 0 0;color:var(--muted)}
 .tag{display:inline-block;background:var(--yellow);color:var(--ink);font:700 .72rem var(--heading);letter-spacing:.08em;text-transform:uppercase;
  padding:.28rem .6rem;border-radius:6px}
 .stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.9rem;margin-bottom:1.2rem}
 .stat{all:unset;box-sizing:border-box;display:flex;flex-direction:column;gap:.15rem;padding:.9rem 1rem;border-radius:14px;background:var(--card);
  border:1px solid var(--line);min-height:112px}
 button.stat{cursor:pointer} button.stat:hover{border-color:var(--yellow)}
 .stat .k{font:600 .8rem var(--heading);color:var(--muted)}
 .stat .v{font:600 2.3rem/1.1 var(--display);letter-spacing:.02em}
 .stat .s{font-size:.85rem;color:var(--muted)}
 .stat.hero{background:var(--yellow);border-color:var(--yellow);color:var(--ink)}
 .stat.hero .k,.stat.hero .s{color:#3F3F46}
 .stat .v.good{color:#16A34A} :root[data-theme=dark] .stat .v.good{color:#4ADE80}
 .stat .v.off{color:var(--muted)}
 .stat .v.hot{color:#DC2626}
 .grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:1.1rem;align-items:start}
 .col{display:grid;gap:1.1rem}
 .card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:1.2rem 1.25rem}
 .card h2{font-size:1.25rem;margin:.6rem 0 .2rem}
 .card .lead{margin:0 0 1rem;color:var(--muted)}
 .presets{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:1rem}
 .chip{all:unset;cursor:pointer;padding:.45rem .8rem;border-radius:999px;border:1px solid var(--line);background:var(--card2);font:600 .85rem var(--heading)}
 .chip:hover{border-color:var(--yellow)}
 .f{display:block;font:600 .85rem var(--heading);margin:.75rem 0 .3rem}
 input[type=text]{font:inherit;width:100%;padding:.7rem .8rem;border:1px solid var(--line);border-radius:10px;color:var(--text);background:var(--field)}
 input[type=text]:focus{outline:3px solid var(--focus);outline-offset:0;border-color:transparent}
 .levels{display:grid;grid-template-columns:repeat(3,1fr);gap:.5rem}
 .levels input{position:absolute;opacity:0;pointer-events:none}
 .levels label{display:flex;flex-direction:column;gap:.1rem;padding:.6rem .7rem;border:2px solid var(--line);border-radius:12px;cursor:pointer;background:var(--card2)}
 .levels label b{font:700 .95rem var(--heading)} .levels label span{font-size:.78rem;color:var(--muted)}
 .levels input:checked+label{border-color:var(--yellow);background:rgba(250,204,21,.14)}
 .levels input:focus-visible+label{outline:3px solid var(--focus);outline-offset:2px}
 .btn{all:unset;box-sizing:border-box;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:.5rem;padding:.75rem 1.2rem;border-radius:10px;
  font:700 1rem var(--heading);background:var(--card2);border:1px solid var(--line)}
 .btn.primary{background:var(--yellow);color:var(--ink);border-color:var(--yellow);width:100%;margin-top:1.1rem;font-size:1.05rem;padding:.9rem 1.2rem}
 .btn.primary:hover{background:var(--amber)}
 .btn:active{transform:translateY(1px)}
 .btn.small{padding:.5rem .85rem;font-size:.88rem}
 .btn.danger{background:var(--alert-bg);color:var(--alert-fg);border-color:transparent}
 .btn[disabled]{opacity:.5;cursor:not-allowed}
 #out{min-height:1.5em;margin-top:.6rem;font-weight:600}
 .ok-text{color:#16A34A} :root[data-theme=dark] .ok-text{color:#4ADE80} .bad-text{color:#DC2626} :root[data-theme=dark] .bad-text{color:#F87171}
 .divider{border:0;border-top:1px dashed var(--line);margin:1.3rem 0 1rem}
 .buzzrow{display:flex;flex-wrap:wrap;align-items:center;gap:.6rem}
 .buzzrow .state{margin-left:auto}
 .pill{display:inline-block;padding:.15rem .6rem;border-radius:999px;font:700 .72rem var(--heading);letter-spacing:.05em;text-transform:uppercase;background:var(--info-bg);color:var(--info-fg)}
 .pill.alert{background:var(--alert-bg);color:var(--alert-fg)} .pill.warn{background:var(--warn-bg);color:var(--warn-fg)}
 .pill.ok{background:var(--ok-bg);color:var(--ok-fg)} .pill.info{background:var(--info-bg);color:var(--info-fg)}
 ol.steps{margin:.6rem 0 0;padding-left:1.2rem} ol.steps li{margin:.35rem 0}
 ul.list{list-style:none;margin:.7rem 0 0;padding:0;display:grid;gap:.5rem}
 ul.list li{display:flex;align-items:center;gap:.6rem;padding:.6rem .75rem;border-radius:10px;background:var(--card2);border:1px solid var(--line);min-width:0}
 ul.list li.empty{justify-content:center;color:var(--muted);border-style:dashed;background:transparent}
 ul.list .grow{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 ul.list .meta{color:var(--muted);font-size:.82rem;white-space:nowrap}
 code{font:600 .9rem ui-monospace,Consolas,monospace;background:var(--card2);border:1px solid var(--line);padding:.1rem .4rem;border-radius:6px}
 pre{font:.82rem/1.45 ui-monospace,Consolas,monospace;background:var(--card2);border:1px solid var(--line);padding:.7rem .8rem;border-radius:10px;overflow:auto;max-height:15rem;white-space:pre-wrap}
 #dash,#vision{height:calc(100vh - 68px)}
 #dash iframe,#vision iframe{width:100%;height:100%;border:0;display:block;background:var(--bg)}
 .msg{max-width:720px;margin:2rem auto;padding:0 1.2rem}
 .msg .card h2{margin-top:.7rem}
 .msg p{color:var(--muted)}
 [hidden]{display:none!important}
 .sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
 @media (max-width:900px){.grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}}
 nav button{white-space:nowrap}
 @media (max-width:560px){.bar{gap:.5rem;padding:0 .6rem}.brand .sub{display:none}.brand .mark{font-size:1.25rem}
  nav{flex:1;min-width:0;overflow-x:auto;scrollbar-width:none}nav button{padding:.4rem .55rem;font-size:.85rem}#theme{flex:none;margin-left:0}
  .page-title h1{font-size:1.45rem}.levels{grid-template-columns:1fr}}
</style>
<header>
 <div class="tape" aria-hidden="true"></div>
 <div class="bar">
  <div class="brand"><span class="mark">SIGHTLINE</span><span class="sub">Control room</span></div>
  <nav role="tablist" aria-label="Pages">
   <button role="tab" id="tab-notify" aria-selected="true" aria-controls="notify">Alerts</button>
   <button role="tab" id="tab-dash" aria-selected="false" aria-controls="dash">Head tracking <span class="led" id="led" title="head-tracking dashboard status"></span></button>
   <button role="tab" id="tab-vision" aria-selected="false" aria-controls="vision">Vision <span class="led" id="vled" title="vision server status"></span></button>
  </nav>
  <button id="theme" type="button" aria-label="Switch to dark mode" title="Switch light / dark">
   <svg class="i-moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>
   <svg class="i-sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.5"/><path d="M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3M4.6 4.6l2.1 2.1M17.3 17.3l2.1 2.1M4.6 19.4l2.1-2.1M17.3 6.7l2.1-2.1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
  </button>
 </div>
</header>

<section id="notify" role="tabpanel" aria-labelledby="tab-notify">
 <main>
  <div class="page-title">
   <div><span class="tag">Phone alerts</span><h1>Is everything ready?</h1><p>Check each part is live, then send a test alert to the worker's phone.</p></div>
  </div>

  <div class="stats" aria-label="System status">
   <div class="stat hero"><span class="k">Phones connected</span><span class="v" id="n">0</span><span class="s" id="n-sub">waiting for the app</span></div>
   <div class="stat"><span class="k">Phone buzz</span><span class="v off" id="buzz-v">Quiet</span><span class="s" id="buzz-s">not buzzing</span></div>
   <button class="stat" type="button" id="stat-dash"><span class="k">Head tracking</span><span class="v off" id="dash-v">Off</span><span class="s" id="dash-s">checking...</span></button>
   <button class="stat" type="button" id="stat-vision"><span class="k">Vision</span><span class="v off" id="vision-v">Off</span><span class="s" id="vision-s">checking...</span></button>
  </div>

  <div class="grid">
   <div class="card">
    <span class="tag">Send an alert</span>
    <h2>Buzz the worker's phone</h2>
    <p class="lead">Pick a ready-made alert or write your own. Every connected phone gets it straight away.</p>
    <div class="presets" aria-label="Ready-made alerts">
     <button class="chip" type="button" data-t="Truck approaching" data-m="Look left" data-l="alert">Truck approaching</button>
     <button class="chip" type="button" data-t="Machine behind you" data-m="Turn around" data-l="alert">Machine behind you</button>
     <button class="chip" type="button" data-t="Heads up" data-m="Machine moving nearby" data-l="warn">Heads up</button>
     <button class="chip" type="button" data-t="All clear" data-m="" data-l="info">All clear</button>
    </div>
    <label class="f" for="t">Title</label><input type="text" id="t" value="Truck approaching" maxlength="120">
    <label class="f" for="m">Message</label><input type="text" id="m" value="Look left" maxlength="500">
    <span class="f" id="lvl-label">How strong</span>
    <div class="levels" role="radiogroup" aria-labelledby="lvl-label">
     <input type="radio" name="level" id="lv-alert" value="alert" checked><label for="lv-alert"><b>Alert</b><span>strong buzz</span></label>
     <input type="radio" name="level" id="lv-warn" value="warn"><label for="lv-warn"><b>Warning</b><span>short buzz</span></label>
     <input type="radio" name="level" id="lv-info" value="info"><label for="lv-info"><b>Info</b><span>no buzz</span></label>
    </div>
    <button class="btn primary" id="send" type="button">Send to phones</button>
    <div id="out" role="status" aria-live="polite"></div>
    <hr class="divider">
    <div class="buzzrow">
     <button class="btn small" id="buzz-test" type="button">Test buzz (2 s)</button>
     <button class="btn small danger" id="buzz-stop" type="button">Stop buzz</button>
     <span class="state pill" id="buzz-pill">quiet</span>
    </div>
   </div>

   <div class="col">
    <div class="card">
     <span class="tag">Connect a phone</span>
     <ol class="steps">
      <li>Join the phone to the same Wi-Fi or hotspot as this laptop.</li>
      <li>Open the <b>SightLine Alert</b> app.</li>
      <li>If it asks for a server, enter one of these:</li>
     </ol>
     <ul class="list" id="urls"><li class="empty">looking for network addresses...</li></ul>
    </div>
    <div class="card">
     <span class="tag">Connected phones</span>
     <ul class="list" id="clients"><li class="empty">no phones yet</li></ul>
    </div>
    <div class="card">
     <span class="tag">Recent alerts</span>
     <ul class="list" id="recent"><li class="empty">nothing sent yet</li></ul>
    </div>
   </div>
  </div>
 </main>
</section>

<section id="dash" role="tabpanel" aria-labelledby="tab-dash" hidden>
 <div id="dash-msg" class="msg" hidden></div>
 <iframe id="dash-frame" title="Head-tracking dashboard" hidden></iframe>
</section>

<section id="vision" role="tabpanel" aria-labelledby="tab-vision" hidden>
 <div id="vision-msg" class="msg" hidden></div>
 <iframe id="vision-frame" title="Vision overlay" hidden></iframe>
</section>

<script>
const q = new URLSearchParams(location.search), token = q.get('token') || '';
const H = token ? {'X-Token': token} : {};
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

// ---- theme (light / dark): saved in this browser, and sent to the dashboard frame
function currentTheme() { return document.documentElement.getAttribute('data-theme'); }
function syncFrame() {
  for (const id of ['dash-frame', 'vision-frame']) {
    const f = $(id);
    if (f.contentWindow) f.contentWindow.postMessage({type: 'sightline-theme', value: currentTheme()}, '*');
  }
}
function applyTheme(t, save) {
  document.documentElement.setAttribute('data-theme', t);
  $('theme').setAttribute('aria-label', t === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
  if (save) { try { localStorage.setItem('sightline-theme', t); } catch (e) {} }
  syncFrame();
}
$('theme').onclick = () => applyTheme(currentTheme() === 'dark' ? 'light' : 'dark', true);
$('dash-frame').addEventListener('load', syncFrame);
$('vision-frame').addEventListener('load', syncFrame);
applyTheme(currentTheme(), false);

// ---- tabs (the chosen tab survives a reload through the URL hash)
function show(name) {
  for (const t of ['notify', 'dash', 'vision']) {
    $('tab-' + t).setAttribute('aria-selected', t === name);
    $(t).hidden = t !== name;
  }
  history.replaceState(null, '', name === 'dash' ? '#dashboard' : name === 'vision' ? '#vision' : location.pathname + location.search);
  if (name === 'dash') pollDash();
  if (name === 'vision') pollVision();
}
$('tab-notify').onclick = () => show('notify');
$('tab-dash').onclick = () => show('dash');
$('tab-vision').onclick = () => show('vision');
$('stat-dash').onclick = () => show('dash');
$('stat-vision').onclick = () => show('vision');
if (location.hash === '#dashboard') show('dash');
if (location.hash === '#vision') show('vision');

function setStat(id, value, cls, sub) {
  $(id + '-v').textContent = value;
  $(id + '-v').className = 'v ' + cls;
  $(id + '-s').textContent = sub;
}
function empty(text) { return '<li class="empty">' + esc(text) + '</li>'; }

// ---- alerts tab
async function copyText(text, btn) {
  try { await navigator.clipboard.writeText(text); btn.textContent = 'Copied'; }
  catch (e) { btn.textContent = 'Select it'; }
  setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
}
fetch('/api/urls', {headers: H}).then(r => r.json()).then(d => {
  $('urls').innerHTML = d.urls.map(u => '<li><code class="grow">' + esc(u) + '</code><button class="btn small" type="button" data-copy="' + esc(u) + '">Copy</button></li>').join('')
    || empty('no network address found: is Wi-Fi on?');
  $('urls').querySelectorAll('[data-copy]').forEach(b => b.onclick = () => copyText(b.dataset.copy, b));
});
async function poll(){
  try {
    const d = await (await fetch('/api/state', {headers: H})).json();
    const n = d.clients.length;
    $('n').textContent = n;
    $('n-sub').textContent = n ? (n === 1 ? 'phone listening' : 'phones listening') : 'waiting for the app';
    $('clients').innerHTML = d.clients.map(c => '<li><span class="led on" aria-hidden="true"></span><span class="grow"><b>' + esc(c.name) + '</b> &middot; ' + esc(c.ip) + '</span><span class="meta">'
      + Math.round(c.connected_s) + ' s</span></li>').join('') || empty('no phones yet: open the app on the phone');
    $('recent').innerHTML = d.recent.map(r => '<li><span class="pill ' + esc(r.level) + '">' + esc(r.level) + '</span><span class="grow"><b>' + esc(r.title) + '</b>'
      + (r.message ? ' &middot; ' + esc(r.message) : '') + '</span><span class="meta">' + new Date(r.sent_at * 1000).toLocaleTimeString() + '</span></li>').join('')
      || empty('nothing sent yet');
    setStat('buzz', d.buzzing ? 'Buzzing' : 'Quiet', d.buzzing ? 'hot' : 'off', d.buzzing ? 'phones are vibrating now' : 'not buzzing');
    $('buzz-pill').textContent = d.buzzing ? 'buzzing' : 'quiet';
    $('buzz-pill').className = 'state pill ' + (d.buzzing ? 'alert' : 'ok');
  } catch (e) {}
}
document.querySelectorAll('.chip').forEach(b => b.onclick = () => {
  $('t').value = b.dataset.t; $('m').value = b.dataset.m;
  $('lv-' + b.dataset.l).checked = true;
  $('send').focus();
});
function level() { const c = document.querySelector('input[name=level]:checked'); return c ? c.value : 'alert'; }
function say(ok, text) { $('out').innerHTML = '<span class="' + (ok ? 'ok-text' : 'bad-text') + '">' + esc(text) + '</span>'; }
$('send').onclick = async () => {
  const body = {v: 1, title: $('t').value, message: $('m').value, level: level()};
  $('send').disabled = true;
  try {
    const r = await fetch('/notify', {method: 'POST', headers: {...H, 'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const d = await r.json();
    if (r.ok) say(d.delivered_to > 0, d.delivered_to > 0 ? 'Sent to ' + d.delivered_to + ' phone(s)' : 'Sent, but no phone is connected yet');
    else say(false, d.error || ('Error ' + r.status));
  } catch (e) { say(false, 'Could not reach the server'); }
  $('send').disabled = false;
  poll();
};
async function buzz(on) {
  try {
    const r = await fetch('/buzz', {method: 'POST', headers: {...H, 'Content-Type': 'application/json'},
      body: JSON.stringify(on ? {v: 1, on: true, hold_s: 2, title: 'Test buzz'} : {v: 1, on: false})});
    const d = await r.json();
    if (!r.ok) say(false, d.error || ('Error ' + r.status));
    else if (on) say(d.delivered_to > 0, d.delivered_to > 0 ? 'Buzzing ' + d.delivered_to + ' phone(s) for 2 s' : 'No phone is connected to buzz');
    else say(true, 'Buzz stopped');
  } catch (e) { say(false, 'Could not reach the server'); }
  poll();
}
$('buzz-test').onclick = () => buzz(true);
$('buzz-stop').onclick = () => buzz(false);

// ---- head-tracking tab: the head-motion dashboard runs on its own port and is shown in a frame
function card(tag, title, html) {
  return '<div class="card"><span class="tag">' + esc(tag) + '</span><h2>' + esc(title) + '</h2>' + html + '</div>';
}
function dashMessage(tag, title, html, log) {
  const m = $('dash-msg');
  m.innerHTML = card(tag, title, html);
  if (log && log.length) { const p = document.createElement('pre'); p.textContent = log.join('\\n'); m.firstChild.appendChild(p); }
  m.hidden = false;
  $('dash-frame').hidden = true;
}
async function pollDash() {
  let d;
  try { d = await (await fetch('/api/dashboard', {headers: H})).json(); } catch (e) { return; }
  $('led').className = 'led' + (d.reachable ? ' on' : '');
  setStat('dash', d.reachable ? 'Live' : (d.managed && d.exit_code === null ? 'Starting' : 'Off'), d.reachable ? 'good' : 'off',
    d.reachable ? 'open the Head tracking tab' : (d.exit_code !== null ? 'stopped (exit ' + d.exit_code + ')' : 'not running'));
  if ($('dash').hidden) return;
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
    dashMessage('Head tracking', 'Running, but only on the laptop', '<p>It only accepts connections from the laptop itself. Open this page on the laptop at <code>http://localhost:' + esc(location.port) + '/#dashboard</code>, or restart the server with <code>--dashboard-lan</code> to open it from this device.</p>');
  } else if (d.exit_code !== null) {
    dashMessage('Head tracking', 'The dashboard stopped (exit code ' + d.exit_code + ')', '<p>Last output:</p>', d.log);
  } else if (d.managed) {
    dashMessage('Head tracking', 'Starting the dashboard...', '<p>The Muse can take up to a minute to connect.</p>', d.log);
  } else if (!d.script_found) {
    dashMessage('Head tracking', 'Dashboard code not found', '<p>Expected <code>' + esc(d.script) + '</code>. Point to it with <code>--dashboard-script</code>.</p>');
  } else {
    dashMessage('Head tracking', 'The dashboard is not running', '<ol class="steps"><li>Restart this server with <code>--dashboard</code>, or</li><li>start it yourself from the repo root:</li></ol><pre>' + esc(d.command) + '</pre>');
  }
}

// ---- vision tab: vision.run_phone serves the overlay page over https on its own port, shown in a frame
async function pollVision() {
  let d;
  try { d = await (await fetch('/api/vision', {headers: H})).json(); } catch (e) { return; }
  const base = 'https://' + location.hostname + ':' + d.port + '/';
  let up = false;
  try { await fetch(base + 'status', {mode: 'no-cors'}); up = true; } catch (e) {}
  $('vled').className = 'led' + (up ? ' on' : '');
  setStat('vision', up ? 'Live' : 'Off', up ? 'good' : 'off', up ? 'open the Vision tab' : 'not running, or certificate not accepted');
  if ($('vision').hidden) return;
  const m = $('vision-msg');
  if (up) {
    if ($('vision-frame').dataset.base !== base) {
      $('vision-frame').dataset.base = base;
      $('vision-frame').src = base + 'overlay?theme=' + currentTheme();
    }
    $('vision-frame').hidden = false;
    m.hidden = true;
  } else {
    m.innerHTML = card('Vision', 'The vision server is not reachable',
      '<ol class="steps"><li>Start it from the repo root:<pre>python -m vision.run_phone --layout floor_layout.json --car-marker 42 --tags-only --muse</pre></li>'
      + '<li>Its page uses a self-signed certificate: open <a href="' + base + 'status" target="_blank" rel="noopener">' + base + '</a> once in this browser and accept the warning.</li>'
      + '<li>Come back here. This tab connects by itself.</li></ol>');
    m.hidden = false;
    $('vision-frame').hidden = true;
    delete $('vision-frame').dataset.base;
  }
}
poll(); setInterval(poll, 2000);
pollVision(); setInterval(pollVision, 4000);
pollDash(); setInterval(pollDash, 3000);
</script>
</html>
"""


def create_app(hub: Optional[Hub] = None, token: Optional[str] = None, port: int = 8765,
               dashboard: Optional[DashboardLink] = None, vision_port: int = 8443) -> Flask:
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

    @app.get("/api/vision")
    def vision_status():
        if not authorised():
            abort(401)
        return jsonify(port=vision_port)

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

    @app.post("/buzz")
    def buzz():
        if not authorised():
            return jsonify(ok=False, error="bad or missing token"), 401
        try:
            msg = clean_buzz(request.get_json(silent=True) or {})
        except ValueError as e:
            return jsonify(ok=False, error=str(e)), 400
        return jsonify(ok=True, on=msg["on"], delivered_to=hub.set_buzz(msg))

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
                    if ev.get("kind") == "buzz":       # no id: buzzes don't move the phone's replay position
                        data = {"v": SCHEMA_VERSION, **{k: ev[k] for k in ("on", "hold_s", "title", "message")}}
                        yield f"event: buzz\ndata: {json.dumps(data)}\n\n"
                        continue
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
    p.add_argument("--vision-port", type=int, default=8443,
                   help="port of the vision server (python -m vision.run_phone) shown in the Vision tab")
    args = p.parse_args(argv)

    hub = Hub()
    dash = DashboardLink(args.dashboard_port, args.dashboard_script, shlex.split(args.dashboard_args), lan=args.dashboard_lan)
    app = create_app(hub, args.token, args.port, dash, args.vision_port)
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
