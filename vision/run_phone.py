"""The vision pipeline (floor map + YOLO + hazard angles) on a phone's camera, streamed over WebRTC.

    python -m vision.run_phone                                   # pixels only (no floor map)
    python -m vision.run_phone --layout floor_layout.json        # calibrate live from floor markers
    python -m vision.run_phone --calib floor_calib.json --layout floor_layout.json   # saved calibration
    python -m vision.run_phone --layout floor_layout.json --car-marker 4            # toy car tagged with marker 4
    python -m vision.run_phone --layout floor_layout.json --car-marker 42 --tags-only   # only worker, floor markers, car tag
    python -m vision.run_phone --layout floor_layout.json --car-marker 42 --tags-only --muse   # heading from the Muse S
    python -m vision.run_phone --layout floor_layout.json --car-marker 42 --worker-marker 10 --tags-only   # hardhat tag 10

It starts Sean's phone-stream server (stream_data/stream_server.py) with this pipeline as its model:
1. Phone and computer on the same Wi-Fi (or the phone's hotspot).
2. Open the printed https://<this computer's IP>:8443 link in Chrome on the phone, accept the
   certificate warning (Advanced -> Proceed), tap Start streaming, allow the camera.
3. The full overlay (floor grid, worker, hazards, distances and angles) is a web page:
   https://localhost:8443/overlay, also the Vision tab of the phone-alert page (http://localhost:8765).
   --window shows it in an OpenCV window instead. https://localhost:8443/view shows the raw stream with boxes.

Each frame's result (worker floor position, each vehicle's distance_m / bearing_deg /
rel_bearing_deg) goes back to the phone, is printed once a second, and is streamed live:
    https://localhost:8443/hazards      Server-Sent Events, one JSON message per frame
    --mqtt HOST                         also publish on sightline/vision/hazards

The toy car is found two ways: YOLO's "car" class (often missed for toys, especially from
above; --vehicle-conf lowers its threshold), and an ArUco tag taped flat on its roof
(--car-marker ID, any id that isn't a floor marker). The tag is reliable; use it.

Worker heading: --muse reads it live from the Muse S over BrainFlow (vision/head_yaw.py, Max's
max/headmotion code). At startup the worker holds still ~5 s facing +x (toward floor marker 1);
yaw 0 = facing +x. Press z (or open /zero) to re-zero whenever they face +x again. Without --muse
the heading is set by hand with a / d.

Overlay page buttons / --window keys: a / d head yaw +-15 deg (with --muse: an offset), z re-zero the
Muse heading, f freeze/unfreeze live calibration, s save calibration, q hide the OpenCV window
(reopen it at /window). Stop the server with Ctrl+C. One phone at a time (the tracker is shared).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import ssl
import sys
import threading
import time
from pathlib import Path

import cv2

from vision.detector import VEHICLE_LABELS, Detector
from vision.floor import FloorLayout, FloorMap
from vision.pipeline import VisionPipeline, scene_to_dict, summary

STREAM_DIR = Path(__file__).resolve().parent.parent / "stream_data"
WIN = "SightLine phone"
log = logging.getLogger("vision.phone")


class HazardHub:
    """Fans every hazard message out to /hazards (Server-Sent Events) clients and, optionally, MQTT.

    publish() is called from the model's worker thread; SSE writes happen on the server's event loop."""

    TOPIC = "sightline/vision/hazards"

    def __init__(self, mqtt_host: str | None = None, topic: str = TOPIC):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queues: set[asyncio.Queue] = set()
        self.topic, self.mqtt = topic, None
        if mqtt_host:
            import paho.mqtt.client as mqtt                    # pip install paho-mqtt
            host, _, port = mqtt_host.partition(":")
            self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            self.mqtt.connect_async(host, int(port or 1883))
            self.mqtt.loop_start()

    def publish(self, msg: dict) -> None:
        line = json.dumps(msg)
        if self.mqtt is not None:
            self.mqtt.publish(self.topic, line)
        if self.loop is not None and self.queues:
            self.loop.call_soon_threadsafe(self._fan_out, line)

    def _fan_out(self, line: str) -> None:
        for q in list(self.queues):
            if q.qsize() < 50:                                   # a stalled client skips frames, never blocks
                q.put_nowait(line)

    def attach(self, app) -> None:
        """Add GET /hazards to the stream server's app."""
        from aiohttp import web

        async def on_startup(app):
            self.loop = asyncio.get_running_loop()

        async def hazards(request):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                               "Access-Control-Allow-Origin": "*"})
            await resp.prepare(request)
            q: asyncio.Queue = asyncio.Queue()
            self.queues.add(q)
            try:
                while True:
                    await resp.write(f"data: {await q.get()}\n\n".encode())
            except (ConnectionResetError, asyncio.CancelledError):
                pass                                             # the client went away
            finally:
                self.queues.discard(q)
            return resp

        app.on_startup.append(on_startup)
        app.router.add_get("/hazards", hazards)

    def close(self) -> None:
        if self.mqtt is not None:
            self.mqtt.loop_stop()
            self.mqtt.disconnect()


class PhoneHazardModel:
    """A stream_data model: model(img_bgr, t_s) -> JSON-ready dict, called from the server's worker thread.

    Every frame's overlay is also JPEG-encoded for the /overlay page. With window=True it is shown
    in an OpenCV window too. All OpenCV GUI calls happen on that same worker thread, which is what
    OpenCV needs.
    """

    def __init__(self, pipeline: VisionPipeline, window: bool = False, save_path: str = "floor_calib.json",
                 layout_path: str | None = None, print_every_s: float = 1.0, hub: HazardHub | None = None,
                 buzzer=None):
        self.pipe, self.window, self.hub, self.buzzer = pipeline, window, hub, buzzer
        self._window_open = False              # an OpenCV window currently exists
        self.save_path, self.layout_path = save_path, layout_path
        self.print_every_s = print_every_s
        self.name = f"sightline vision ({getattr(pipeline.detector, 'weights', 'yolo')})"
        self._lock = threading.Lock()          # two phones would share one tracker: serialise them
        self._last_print = 0.0
        self._fps, self._t_prev = 0.0, None
        self.overlay_jpg: bytes | None = None
        self.overlay_seq = 0
        self.overlay_watchers = 0              # /overlay.mjpg clients; no one watching = no encoding

    def __call__(self, img, t_s: float) -> dict:
        with self._lock:
            res = self.pipe.process(img, t_s)
            out = scene_to_dict(res, self.pipe.head_yaw_deg, self.pipe.facing_deg,
                                include_others=not self.pipe.tags_only, pipe=self.pipe)
            out["t_s"] = t_s
            if self.hub is not None:
                self.hub.publish({k: v for k, v in out.items() if k != "detections"})   # boxes are /view-only
            if self.buzzer is not None:
                self.buzzer.update(out)
            now = time.perf_counter()
            if self._t_prev is not None:
                inst = 1 / max(now - self._t_prev, 1e-6)
                self._fps = 0.9 * self._fps + 0.1 * inst if self._fps else inst
            self._t_prev = now
            if self.print_every_s and now - self._last_print > self.print_every_s:
                print(summary(res.scene), flush=True)
                self._last_print = now
            if self.window or self.overlay_watchers:
                view = self._render(img, res)
                if self.overlay_watchers:
                    ok, jpg = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if ok:
                        self.overlay_jpg, self.overlay_seq = jpg.tobytes(), self.overlay_seq + 1
                if self.window:
                    self._show(view)
            return out

    def _render(self, img, res):
        h, w = img.shape[:2]
        yaw = "--" if self.pipe.head_yaw_deg is None else f"{self.pipe.head_yaw_deg:+.0f}"
        return self.pipe.render(img, res, f"{w}x{h} {self._fps:4.1f} FPS  YOLO {res.infer_ms:3.0f} ms | "
                                          f"{res.calib_status} | head yaw {yaw} ({self.pipe.heading_status})")

    def save_calibration(self) -> bool:
        if self.pipe.floor_map is None:
            return False
        self.pipe.floor_map.save(self.save_path, layout=self.layout_path)
        print("saved", Path(self.save_path).resolve())
        return True

    def _show(self, view) -> None:
        if self._window_open and cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
            self._hide("window closed")              # closed with its X button
            return
        if not self._window_open:
            cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
            self._window_open = True
        cv2.imshow(WIN, view)
        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            self._hide("window hidden")
        elif k == ord("a"):
            self.pipe.nudge_yaw(15)
        elif k == ord("d"):
            self.pipe.nudge_yaw(-15)
        elif k == ord("z"):
            self.pipe.zero_heading()
        elif k == ord("f"):
            self.pipe.toggle_freeze()
        elif k == ord("s"):
            self.save_calibration()

    def _hide(self, why: str) -> None:
        try:
            cv2.destroyWindow(WIN)
        except cv2.error:
            pass
        cv2.waitKey(1)
        self._window_open = self.window = False
        print(f"{why}; reopen it at https://localhost:<port>/window. Results still go to the phone, "
              "/overlay, /hazards and the terminal (Ctrl+C stops)", flush=True)

    def attach(self, app) -> None:
        """Add GET /overlay (page), /overlay.mjpg (video), /control/<action>, /window, /zero and
        GET/POST /buzz (buzz settings: {"enabled": bool, "every_s": seconds}) and GET/POST /fov
        (the worker's field of view for FACING: {"fov_deg": degrees}, = 2 x --facing-deg)."""
        from aiohttp import web

        async def reopen(request):
            self.window = True
            return web.Response(text="SightLine phone window reopens with the next frame. You can close this tab.\n")

        def zero_response():
            if self.pipe.heading is None:
                return web.Response(status=409, text="no live heading (start with --muse)\n")
            self.pipe.zero_heading()
            return web.Response(text="Muse heading re-zeroed: the worker now faces +x (0 deg).\n")

        async def zero(request):
            return zero_response()

        async def page(request):
            return web.Response(text=OVERLAY_HTML, content_type="text/html")

        async def video(request):
            """MJPEG of the full overlay, for an <img> tag."""
            resp = web.StreamResponse(headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame",
                                               "Cache-Control": "no-cache"})
            await resp.prepare(request)
            last = 0
            self.overlay_watchers += 1
            try:
                while True:
                    if self.overlay_seq != last and self.overlay_jpg is not None:
                        last, jpg = self.overlay_seq, self.overlay_jpg
                        await resp.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                    await asyncio.sleep(0.03)
            except (ConnectionResetError, asyncio.CancelledError):
                pass                                             # the viewer closed the page
            finally:
                self.overlay_watchers -= 1
            return resp

        async def control(request):
            act = request.match_info["action"]
            if act == "zero":
                return zero_response()
            if act == "freeze":
                self.pipe.toggle_freeze()
            elif act == "save":
                if not self.save_calibration():
                    return web.Response(status=409, text="no floor map to save\n")
            elif act in ("left", "right"):
                self.pipe.nudge_yaw(15 if act == "left" else -15)
            else:
                raise web.HTTPNotFound(text="unknown action\n")
            return web.Response(text="ok\n")

        async def buzz_get(request):
            if self.buzzer is None:
                return web.json_response({"available": False})
            return web.json_response({"available": True, **self.buzzer.settings()})

        async def buzz_set(request):
            if self.buzzer is None:
                return web.json_response({"available": False, "error": "start the server with --buzz"}, status=409)
            try:
                d = await request.json()
                every = d.get("every_s")
                s = self.buzzer.configure(None if every is None else float(every), d.get("enabled"), d.get("level"),
                                          d.get("mode"))
            except (ValueError, TypeError) as e:
                return web.json_response({"available": True, "error": str(e)}, status=400)
            how = "continuous" if s["mode"] == "continuous" else f"pulse every {s['every_s']:g} s, {s['level']}"
            print(f"[buzz] {'on' if s['enabled'] else 'off'}, {how} (set from the overlay page)", flush=True)
            return web.json_response({"available": True, **s})

        def fov_state():
            return {"fov_deg": round(2 * self.pipe.facing_deg, 1), "min_fov_deg": 10, "max_fov_deg": 360}

        async def fov_get(request):
            return web.json_response(fov_state())

        async def fov_set(request):
            """{"fov_deg": N}: the worker counts as FACING a hazard inside +-N/2 deg of their heading."""
            try:
                fov = float((await request.json())["fov_deg"])
            except (ValueError, TypeError, KeyError) as e:
                return web.json_response({"error": f"send {{\"fov_deg\": number}} ({e})"}, status=400)
            self.pipe.facing_deg = min(max(fov, 10.0), 360.0) / 2
            print(f"[fov] facing = within +-{self.pipe.facing_deg:g} deg of the heading "
                  f"(field of view {2 * self.pipe.facing_deg:g} deg, set from the overlay page)", flush=True)
            return web.json_response(fov_state())

        def motion_state():
            return {"cone_deg": round(2 * self.pipe.approach_cone_deg, 1), "min_speed_mps": self.pipe.min_speed_mps}

        async def motion_get(request):
            return web.json_response(motion_state())

        async def motion_set(request):
            """{"cone_deg": N, "min_speed_mps": v}: a hazard faster than v is HEADING AT the worker when the
            worker is within +-N/2 deg of its direction of travel."""
            try:
                d = await request.json()
                if d.get("cone_deg") is not None:
                    self.pipe.approach_cone_deg = min(max(float(d["cone_deg"]), 10.0), 360.0) / 2
                if d.get("min_speed_mps") is not None:
                    self.pipe.min_speed_mps = min(max(float(d["min_speed_mps"]), 0.0), 5.0)
            except (ValueError, TypeError, AttributeError) as e:
                return web.json_response({"error": f"send {{\"cone_deg\": n, \"min_speed_mps\": v}} ({e})"}, status=400)
            print(f"[motion] heading at the worker = within +-{self.pipe.approach_cone_deg:g} deg of the car's travel, "
                  f"faster than {self.pipe.min_speed_mps:g} m/s (set from the overlay page)", flush=True)
            return web.json_response(motion_state())

        app.router.add_get("/motion", motion_get)
        app.router.add_post("/motion", motion_set)
        app.router.add_get("/fov", fov_get)
        app.router.add_post("/fov", fov_set)
        app.router.add_get("/buzz", buzz_get)
        app.router.add_post("/buzz", buzz_set)
        app.router.add_get("/window", reopen)
        app.router.add_get("/zero", zero)
        app.router.add_get("/overlay", page)
        app.router.add_get("/overlay.mjpg", video)
        app.router.add_post(r"/control/{action:\w+}", control)


OVERLAY_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SightLine overlay</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
<style>
/* Colours and fonts from the SightLine pitch deck (same as the control page on port 8765) */
:root{color-scheme:light;--tape-y:#FFCC4D;--tape-d:#292F33;--yellow:#FACC15;--amber:#FFBF00;--ink:#09090B;
 --bg:#F4F4F5;--card:#FFFFFF;--card2:#FAFAFA;--text:#18181B;--muted:#52525B;--line:#E4E4E7;--field:#FFFFFF;
 --heading:'Space Grotesk','Segoe UI',system-ui,sans-serif;--body:'Inter','Segoe UI',system-ui,sans-serif}
:root[data-theme=dark]{color-scheme:dark;--bg:#393939;--card:#27272A;--card2:#1F1F22;--text:#FAFAFA;--muted:#A1A1AA;--line:#52525B;--field:#18181B}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 var(--body);display:flex;flex-direction:column;height:100vh}
.tape{flex:none;height:8px;background:repeating-linear-gradient(-45deg,var(--tape-y) 0 12px,var(--tape-d) 12px 24px)}
.layout{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:14px;padding:14px}
.stage{min-height:0;display:flex;flex-direction:column;gap:10px}
.stagehead{display:flex;flex-wrap:wrap;align-items:center;gap:10px}
.tag{display:inline-block;background:var(--yellow);color:var(--ink);font:700 .72rem var(--heading);letter-spacing:.08em;text-transform:uppercase;padding:.28rem .6rem;border-radius:6px}
#msg{color:var(--muted);font-weight:500}
#v{flex:1;min-height:0;display:flex;justify-content:center;align-items:center;background:#09090B;border-radius:14px;overflow:hidden}
img{max-width:100%;max-height:100%;object-fit:contain;display:block}
aside{min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:12px;padding-right:2px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}
.card h3{margin:0 0 .25rem;font:700 1rem var(--heading)}
.hint{margin:0 0 .7rem;color:var(--muted);font-size:.85rem}
.btns{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:6px}
.btns.two{grid-template-columns:1fr 1fr}
button{font:600 .85rem var(--heading);padding:.55rem .5rem;border:1px solid var(--line);border-radius:9px;background:var(--card2);color:var(--text);cursor:pointer}
button:hover{border-color:var(--yellow)}
button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid var(--amber);outline-offset:2px}
button.primary{grid-column:1 / -1;background:var(--yellow);border-color:var(--yellow);color:var(--ink);font-size:.95rem;padding:.7rem}
button.primary:hover{background:var(--amber)}
kbd{font:600 .72rem ui-monospace,Consolas,monospace;padding:.05rem .3rem;border-radius:4px;border:1px solid currentColor;opacity:.6;margin-left:.25rem}
.range{display:flex;align-items:center;gap:10px}
.range input{flex:1;accent-color:#EAB308}
.val{min-width:3.4em;text-align:right;font:700 1.05rem var(--heading);font-variant-numeric:tabular-nums}
.row{display:flex;align-items:center;gap:8px;margin-top:.6rem;flex-wrap:wrap}
input[type=number],select{font:inherit;padding:.4rem .5rem;border:1px solid var(--line);border-radius:8px;background:var(--field);color:var(--text)}
input[type=number]{width:5em}
select{flex:1;min-width:0}
#buzz{display:none}
.switch{display:flex;align-items:center;gap:10px;font:700 .95rem var(--heading);cursor:pointer}
.switch input{width:2.6rem;height:1.4rem;appearance:none;border-radius:999px;background:var(--line);position:relative;cursor:pointer;margin:0;flex:none}
.switch input::after{content:"";position:absolute;top:3px;left:3px;width:calc(1.4rem - 6px);height:calc(1.4rem - 6px);border-radius:50%;background:#fff;transition:left .15s}
.switch input:checked{background:#16A34A}
.switch input:checked::after{left:calc(100% - 1.4rem + 3px)}
@media (max-width:900px){body{height:auto}.layout{grid-template-columns:1fr}#v{min-height:55vh}aside{overflow:visible}}
</style></head><body>
<div class="tape" aria-hidden="true"></div>
<div class="layout">
 <div class="stage">
  <div class="stagehead"><span class="tag">Live overlay</span><span id="msg" role="status">Waiting for the phone: open the server's https link on it and tap Start streaming.</span></div>
  <div id="v"><img id="img" alt="Camera view with the floor grid, worker heading and hazards drawn on top"></div>
 </div>
 <aside aria-label="Tuning">
  <section class="card">
   <h3>Line up the heading</h3>
   <p class="hint">Stand the worker facing marker 1 (+x), then press Zero. Nudge with the turn buttons if the arrow drifts.</p>
   <div class="btns two">
    <button class="primary" data-a="zero" title="worker faces +x (toward marker 1)">Zero heading<kbd>Z</kbd></button>
    <button data-a="left" title="turn the heading 15 degrees left">&#8634; Left 15&deg;<kbd>A</kbd></button>
    <button data-a="right" title="turn the heading 15 degrees right">Right 15&deg; &#8635;<kbd>D</kbd></button>
    <button data-a="freeze" title="stop adjusting the heading calibration">Freeze<kbd>F</kbd></button>
    <button data-a="save" title="save the calibration to disk">Save<kbd>S</kbd></button>
   </div>
  </section>
  <section class="card" id="fov" title="Worker's field of view: a hazard inside it counts as FACING">
   <h3><label for="fovIn">Worker's field of view</label></h3>
   <p class="hint">A hazard inside this angle counts as seen.</p>
   <div class="range"><input type="range" id="fovIn" min="10" max="360" step="5" value="60"><span class="val" id="fovVal">60&deg;</span></div>
  </section>
  <section class="card" id="motion" title="The car is HEADING AT the worker when the worker is inside this cone around the car's direction of travel and the car is faster than the minimum speed">
   <h3><label for="coneIn">When is the car heading at them?</label></h3>
   <p class="hint">The worker must be inside this cone around the car's direction of travel, and the car faster than the minimum speed.</p>
   <div class="range"><input type="range" id="coneIn" min="10" max="360" step="5" value="60"><span class="val" id="coneVal">60&deg;</span></div>
   <div class="row"><label for="minSpeed">Minimum speed</label><input type="number" id="minSpeed" min="0" max="5" step="0.05" value="0.1"> m/s</div>
  </section>
  <section class="card" id="buzz" title="Buzz the phone while the car is heading at the worker and the worker is not facing it">
   <h3>Phone buzz</h3>
   <p class="hint">Buzzes while the car is heading at the worker and they haven't looked at it.</p>
   <label class="switch"><input type="checkbox" id="buzzOn"> Buzz the phone</label>
   <div class="row">
    <select id="buzzMode" aria-label="Buzz style" title="continuous: buzzes (sound + vibration) for exactly as long as the worker isn't facing it (app 1.1+). pulse: one notification every N s (older apps)">
     <option value="continuous">Continuous</option><option value="pulse-warn">Pulse, short</option><option value="pulse-alert">Pulse, long</option></select>
    <span id="buzzEveryBox">every <input type="number" id="buzzEvery" min="0.2" max="600" step="0.5" aria-label="Seconds between pulses"> s</span>
   </div>
  </section>
 </aside>
</div>
<script>
const $=id=>document.getElementById(id);
const t=new URLSearchParams(location.search).get('theme');
if(t)document.documentElement.setAttribute('data-theme',t);
addEventListener('message',e=>{if(e.data&&e.data.type==='sightline-theme')document.documentElement.setAttribute('data-theme',e.data.value)});
async function act(a){const r=await fetch('/control/'+a,{method:'POST'});$('msg').textContent=(await r.text()).trim()}
document.querySelectorAll('button[data-a]').forEach(b=>b.onclick=()=>act(b.dataset.a));
function showBuzz(s){if(!s.available)return;$('buzz').style.display='block';$('buzzOn').checked=s.enabled;
 if(document.activeElement!==$('buzzEvery'))$('buzzEvery').value=s.every_s;
 if(s.mode)$('buzzMode').value=s.mode==='continuous'?'continuous':'pulse-'+s.level;
 $('buzzEveryBox').style.display=s.mode==='pulse'?'inline':'none';
 if(s.error)$('msg').textContent=s.error}
async function setBuzz(body){const r=await fetch('/buzz',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const s=await r.json();showBuzz(s);if(r.ok)$('msg').textContent=`Buzz ${s.enabled?'on':'off'}, `+(s.mode==='continuous'?'continuous':`pulse every ${s.every_s} s`)}
$('buzzOn').onchange=()=>setBuzz({enabled:$('buzzOn').checked});
$('buzzMode').onchange=()=>{const v=$('buzzMode').value;setBuzz(v==='continuous'?{mode:'continuous'}:{mode:'pulse',level:v.slice(6)})};
$('buzzEvery').onchange=()=>{const v=parseFloat($('buzzEvery').value);if(v>0)setBuzz({every_s:v})};
fetch('/buzz').then(r=>r.json()).then(showBuzz).catch(()=>{});
function showFov(s){if(s.fov_deg==null)return;$('fovIn').value=s.fov_deg;$('fovVal').textContent=Math.round(s.fov_deg)+'°'}
let fovTimer=null;
$('fovIn').oninput=()=>{$('fovVal').textContent=$('fovIn').value+'°';clearTimeout(fovTimer);
 fovTimer=setTimeout(async()=>{const r=await fetch('/fov',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({fov_deg:+$('fovIn').value})});const s=await r.json();showFov(s);
  if(r.ok)$('msg').textContent=`Facing = within ±${s.fov_deg/2}° of the heading`},150)};
fetch('/fov').then(r=>r.json()).then(showFov).catch(()=>{});
function showMotion(s){if(s.cone_deg==null)return;$('coneIn').value=s.cone_deg;$('coneVal').textContent=Math.round(s.cone_deg)+'°';
 if(document.activeElement!==$('minSpeed'))$('minSpeed').value=s.min_speed_mps}
async function setMotion(body){const r=await fetch('/motion',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const s=await r.json();showMotion(s);if(r.ok)$('msg').textContent=`Heading at worker = within ±${s.cone_deg/2}° of the car's travel, faster than ${s.min_speed_mps} m/s`}
let coneTimer=null;
$('coneIn').oninput=()=>{$('coneVal').textContent=$('coneIn').value+'°';clearTimeout(coneTimer);
 coneTimer=setTimeout(()=>setMotion({cone_deg:+$('coneIn').value}),150)};
$('minSpeed').onchange=()=>{const v=parseFloat($('minSpeed').value);if(v>=0)setMotion({min_speed_mps:v})};
fetch('/motion').then(r=>r.json()).then(showMotion).catch(()=>{});
addEventListener('keydown',e=>{if(e.target.tagName==='INPUT'||e.target.tagName==='SELECT')return;const m={z:'zero',a:'left',d:'right',f:'freeze',s:'save'}[e.key];if(m)act(m)});
const img=$('img');
function connect(){img.src='/overlay.mjpg?'+Date.now()}
img.onload=()=>{img.style.visibility='visible';$('msg').textContent='Live'};
img.onerror=()=>{img.style.visibility='hidden';$('msg').textContent='No overlay yet: waiting for the phone...';setTimeout(connect,2000)};
connect();
</script></body></html>
"""


def import_stream_server():
    """stream_data/stream_server.py does `from model import ...`, so its folder must be on sys.path."""
    if str(STREAM_DIR) not in sys.path:
        sys.path.insert(0, str(STREAM_DIR))
    import stream_server                            # noqa: E402  (stream_data/stream_server.py)
    return stream_server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_argument_group("vision")
    g.add_argument("--model", default="yolo26n.pt", help="YOLO weights (downloaded on first use)")
    g.add_argument("--conf", type=float, default=0.35, help="YOLO threshold for people")
    g.add_argument("--vehicle-conf", type=float, default=0.25,
                   help="YOLO threshold for vehicles (toy cars score low; below 0.25 also pass --no-track)")
    g.add_argument("--car-marker", action="append", default=[], metavar="ID|auto",
                   help="ArUco id taped on the toy car (repeatable), or 'auto' = any non-floor marker; "
                        "its centre is the car's position")
    g.add_argument("--car-label", default="toy car", help="label for marker-tagged vehicles")
    g.add_argument("--worker-marker", type=int, metavar="ID",
                   help="ArUco id taped on top of the worker's hardhat: that person is the worker, with a fixed id "
                        "(the marker itself stands in when YOLO misses the person); not a floor or car marker")
    g.add_argument("--blob-fallback", action="store_true",
                   help="follow the dark blob under the car while its marker can't be read (dark car, light floor)")
    g.add_argument("--imgsz", type=int, default=640)
    g.add_argument("--device", default=None,
                   help="where YOLO runs: 0 = first NVIDIA GPU, cpu = processor; default: the GPU if PyTorch can use one")
    g.add_argument("--marker-width", type=int, default=960,
                   help="search ArUco markers on a copy this many pixels wide, then refine on the full frame "
                        "(~3x faster at 1080p); 0 = full resolution (try it if small/far markers are missed)")
    g.add_argument("--vehicles", nargs="+", default=sorted(VEHICLE_LABELS), help="COCO classes treated as vehicles")
    g.add_argument("--tags-only", action="store_true",
                   help="show only the worker, floor markers and --car-marker tags (YOLO detects people only)")
    g.add_argument("--facing-deg", type=float, default=30.0,
                   help="worker counts as FACING the car when |relative angle| <= this (half the field of view; "
                        "the overlay page's Field of view slider changes it live)")
    g.add_argument("--approach-deg", type=float, default=30.0,
                   help="the car is HEADING AT the worker when |approach angle| <= this (half the car heading cone; "
                        "the overlay page's Car heading cone slider changes it live)")
    g.add_argument("--min-speed", type=float, default=0.1,
                   help="m/s; a slower car counts as stopped (never heading at anyone)")
    g.add_argument("--no-track", action="store_true", help="detection only, no ByteTrack IDs")
    g.add_argument("--layout", help="floor_layout.json: calibrate live from floor markers")
    g.add_argument("--calib", help="floor_calib.json saved by check_floor / run_vision (fixed calibration)")
    g.add_argument("--save", default="floor_calib.json")
    g.add_argument("--head-yaw", type=float, default=0.0)
    g.add_argument("--print-every", type=float, default=1.0, help="console summary interval (s), 0 = off")
    g.add_argument("--window", action="store_true", help="also show the overlay in an OpenCV window (default: web page at /overlay)")
    g.add_argument("--mqtt", metavar="HOST[:PORT]", help="also publish each message on sightline/vision/hazards")
    g.add_argument("--buzz", nargs="?", const="http://127.0.0.1:8765", metavar="URL",
                   help="buzz the phone through the phone-alert server (default http://127.0.0.1:8765) while the "
                        "worker is not facing a hazard (see --buzz-mode)")
    g.add_argument("--buzz-mode", choices=["continuous", "pulse"], default="continuous",
                   help="continuous: the app buzzes until the worker faces the hazard (app 1.1+); "
                        "pulse: one notification every --buzz-every seconds (older apps). Changeable on the overlay page")
    g.add_argument("--buzz-every", type=float, default=1.0,
                   help="pulse mode: seconds between buzzes (also adjustable on the overlay page)")
    g.add_argument("--alert-token", help="the phone-alert server's --token, if it has one")
    m = ap.add_argument_group("worker heading (Muse S)")
    m.add_argument("--muse", nargs="?", const="muse", metavar="SOURCE",
                   help="live heading from the Muse: muse (default) | dashboard (the running head-motion dashboard "
                        "owns the Muse and this reads its yaw) | synthetic:<scene> | replay:<csv>")
    m.add_argument("--dashboard-url", default="http://127.0.0.1:5000", help="where --muse dashboard reads the yaw")
    m.add_argument("--muse-board", default=None, choices=["muse_s", "muse_s_bled", "synthetic"],
                   help="muse_s = laptop Bluetooth (default); muse_s_bled = BLED112 dongle; synthetic = BrainFlow fake")
    m.add_argument("--muse-name", help="Bluetooth name of the band (e.g. MuseS-1A2B) if several are around")
    m.add_argument("--muse-mac", help="Bluetooth address of the band, instead of --muse-name")
    m.add_argument("--muse-port", help="COM port of the BLED112 dongle (--muse-board muse_s_bled)")
    m.add_argument("--muse-bias", type=float, default=5.0, help="seconds the worker holds still at startup")
    m.add_argument("--muse-flip", action="store_true", help="negate the yaw (if turning left makes it go down)")
    s = ap.add_argument_group("phone stream")
    s.add_argument("--host", default="0.0.0.0", help="0.0.0.0 = reachable from the phone on the same network")
    s.add_argument("--port", type=int, default=8443)
    s.add_argument("--view-fps", type=float, default=15.0, help="frame rate of the /view page")
    s.add_argument("--no-https", action="store_true", help="plain HTTP (the phone camera then won't work)")
    s.add_argument("--new-cert", action="store_true", help="regenerate the self-signed certificate (e.g. new IP)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("aioice", "aiortc", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    server = import_stream_server()
    from aiohttp import web

    layout = FloorLayout.load(args.layout) if args.layout else None
    fm = FloorMap.load(args.calib) if args.calib else None
    det = Detector(args.model, args.conf, args.imgsz, not args.no_track, [] if args.tags_only else args.vehicles,
                   vehicle_conf=args.vehicle_conf, device=args.device)
    auto = "auto" in args.car_marker
    tags = {int(i): args.car_label for i in args.car_marker if i != "auto"}
    pipe = VisionPipeline(det, layout, fm, args.head_yaw, hazard_markers=tags,
                          any_marker_label=args.car_label if auto else None,
                          tags_only=args.tags_only, facing_deg=args.facing_deg, blob_fallback=args.blob_fallback,
                          worker_marker=args.worker_marker,
                          marker_max_width=args.marker_width or None,
                          approach_cone_deg=args.approach_deg, min_speed_mps=args.min_speed)
    heading = None
    if args.muse == "dashboard":
        from vision.dashboard_heading import DashboardHeading
        heading = DashboardHeading(args.dashboard_url, flip=args.muse_flip).start()
        pipe.heading = heading
    elif args.muse:
        from vision.head_yaw import MuseHeading
        heading = MuseHeading(args.muse, bias_s=args.muse_bias, flip=args.muse_flip, board=args.muse_board,
                              name=args.muse_name, mac=args.muse_mac, serial_port=args.muse_port).start()
        pipe.heading = heading
    hub = HazardHub(args.mqtt)
    buzzer = None
    if args.buzz:
        from vision.buzz import PhoneBuzzer
        buzzer = PhoneBuzzer(args.buzz, args.buzz_every, args.alert_token, mode=args.buzz_mode)
    model = PhoneHazardModel(pipe, window=args.window, save_path=args.save, layout_path=args.layout,
                             print_every_s=args.print_every, hub=hub, buzzer=buzzer)

    ip = server.lan_ip()
    ssl_ctx = None
    if not args.no_https:
        cert, key = STREAM_DIR / "cert.pem", STREAM_DIR / "key.pem"
        server.ensure_cert(cert, key, ip, args.new_cert)
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(cert, key)
    scheme = "http" if args.no_https else "https"
    print(f"\nmodel: {model.name}")
    import torch
    gpu = torch.cuda.is_available()
    dev = args.device if args.device is not None else ("0" if gpu else "cpu")
    print(f"YOLO on: {'GPU ' + torch.cuda.get_device_name(0) if dev != 'cpu' and gpu else 'CPU'}"
          + ("" if gpu else " (PyTorch has no CUDA: install the CUDA build to use the GPU)")
          + f" | markers searched at {args.marker_width or 'full'} px wide")
    print(f"floor: {'saved calibration ' + args.calib if fm else 'live from ' + args.layout if layout else 'none (pixels only)'}")
    print(f"open on the phone (same Wi-Fi):  {scheme}://{ip}:{args.port}")
    print(f"raw stream + boxes on this PC:   {scheme}://localhost:{args.port}/view")
    print(f"live angles (SSE):               {scheme}://localhost:{args.port}/hazards")
    print(f"overlay page (Vision tab):       {scheme}://localhost:{args.port}/overlay")
    print(f"vehicles: YOLO {sorted(det.vehicle_labels)} >= {det.vehicle_conf}"
          + (f", tagged {args.car_label} = marker {args.car_marker}" if tags or auto else ""))
    print("worker: " + (f"the person with hardhat marker {args.worker_marker}" if args.worker_marker is not None
                        else "the most confident tracked person"))
    print("heading: " + (f"{'dashboard at ' + args.dashboard_url if args.muse == 'dashboard' else 'Muse (' + args.muse + ')'}; re-zero facing +x: z key or {scheme}://localhost:{args.port}/zero"
                         if heading else "manual, a / d keys"))
    if args.mqtt:
        print(f"mqtt: {HazardHub.TOPIC} on {args.mqtt}")
    if buzzer:
        how = "continuously" if buzzer.mode == "continuous" else f"every {buzzer.every_s:g} s"
        print(f"buzz: {how} while the car is heading at the worker and not faced, via {args.buzz} (needs a phone connected); "
              "change it on the overlay page")
    print(f"status:                          {scheme}://localhost:{args.port}/status\n")
    app = server.create_app(model, args.view_fps)
    hub.attach(app)
    model.attach(app)
    try:
        web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_ctx, print=None)
    finally:
        hub.close()
        if heading is not None:
            heading.stop()


if __name__ == "__main__":
    main()
