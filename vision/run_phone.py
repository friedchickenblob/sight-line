"""The vision pipeline (floor map + YOLO + hazard angles) on a phone's camera, streamed over WebRTC.

    python -m vision.run_phone                                   # pixels only (no floor map)
    python -m vision.run_phone --layout floor_layout.json        # calibrate live from floor markers
    python -m vision.run_phone --calib floor_calib.json --layout floor_layout.json   # saved calibration
    python -m vision.run_phone --layout floor_layout.json --car-marker 4            # toy car tagged with marker 4

It starts Sean's phone-stream server (stream_data/server.py) with this pipeline as its model:
1. Phone and computer on the same Wi-Fi (or the phone's hotspot).
2. Open the printed https://<this computer's IP>:8443 link in Chrome on the phone, accept the
   certificate warning (Advanced -> Proceed), tap Start streaming, allow the camera.
3. This computer opens a "SightLine phone" window with the full overlay (floor grid, worker,
   hazards, distances and angles). https://localhost:8443/view shows the raw stream with boxes.

Each frame's result (worker floor position, each vehicle's distance_m / bearing_deg /
rel_bearing_deg) goes back to the phone, is printed once a second, and is streamed live:
    https://localhost:8443/hazards      Server-Sent Events, one JSON message per frame
    --mqtt HOST                         also publish on sightline/vision/hazards

The toy car is found two ways: YOLO's "car" class (often missed for toys, especially from
above; --vehicle-conf lowers its threshold), and an ArUco tag taped flat on its roof
(--car-marker ID, any id that isn't a floor marker). The tag is reliable; use it.

Window keys: a / d head yaw +-15 deg, f freeze/unfreeze live calibration, s save calibration,
q hide the window. Stop the server with Ctrl+C. One phone at a time (the tracker is shared).
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

    With window=True it also shows the overlay in an OpenCV window. All OpenCV GUI calls happen
    on that same worker thread, which is what OpenCV needs.
    """

    def __init__(self, pipeline: VisionPipeline, window: bool = True, save_path: str = "floor_calib.json",
                 layout_path: str | None = None, print_every_s: float = 1.0, hub: HazardHub | None = None):
        self.pipe, self.window, self.hub = pipeline, window, hub
        self.save_path, self.layout_path = save_path, layout_path
        self.print_every_s = print_every_s
        self.name = f"sightline vision ({getattr(pipeline.detector, 'weights', 'yolo')})"
        self._lock = threading.Lock()          # two phones would share one tracker: serialise them
        self._last_print = 0.0
        self._fps, self._t_prev = 0.0, None

    def __call__(self, img, t_s: float) -> dict:
        with self._lock:
            res = self.pipe.process(img)
            out = scene_to_dict(res, self.pipe.head_yaw_deg)
            out["t_s"] = t_s
            if self.hub is not None:
                self.hub.publish({k: v for k, v in out.items() if k != "detections"})   # boxes are /view-only
            now = time.perf_counter()
            if self._t_prev is not None:
                inst = 1 / max(now - self._t_prev, 1e-6)
                self._fps = 0.9 * self._fps + 0.1 * inst if self._fps else inst
            self._t_prev = now
            if self.print_every_s and now - self._last_print > self.print_every_s:
                print(summary(res.scene), flush=True)
                self._last_print = now
            if self.window:
                self._show(img, res)
            return out

    def _show(self, img, res) -> None:
        h, w = img.shape[:2]
        view = self.pipe.render(img, res, f"{w}x{h} {self._fps:4.1f} FPS  YOLO {res.infer_ms:3.0f} ms | "
                                          f"{res.calib_status} | head yaw {self.pipe.head_yaw_deg:+.0f} (a/d) | "
                                          f"f freeze  s save  q hide")
        cv2.imshow(WIN, view)
        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            cv2.destroyWindow(WIN)
            self.window = False
            print("window hidden; results still go to the phone and the terminal (Ctrl+C stops)")
        elif k == ord("a"):
            self.pipe.nudge_yaw(15)
        elif k == ord("d"):
            self.pipe.nudge_yaw(-15)
        elif k == ord("f"):
            self.pipe.toggle_freeze()
        elif k == ord("s") and self.pipe.floor_map is not None:
            self.pipe.floor_map.save(self.save_path, layout=self.layout_path)
            print("saved", Path(self.save_path).resolve())


def import_stream_server():
    """stream_data/server.py does `from model import ...`, so its folder must be on sys.path."""
    if str(STREAM_DIR) not in sys.path:
        sys.path.insert(0, str(STREAM_DIR))
    import server                                   # noqa: E402  (stream_data/server.py)
    return server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_argument_group("vision")
    g.add_argument("--model", default="yolo26n.pt", help="YOLO weights (downloaded on first use)")
    g.add_argument("--conf", type=float, default=0.35, help="YOLO threshold for people")
    g.add_argument("--vehicle-conf", type=float, default=0.25,
                   help="YOLO threshold for vehicles (toy cars score low; below 0.25 also pass --no-track)")
    g.add_argument("--car-marker", type=int, action="append", default=[], metavar="ID",
                   help="ArUco id taped on the toy car (repeatable); its centre is the car's position")
    g.add_argument("--car-label", default="toy car", help="label for marker-tagged vehicles")
    g.add_argument("--imgsz", type=int, default=640)
    g.add_argument("--vehicles", nargs="+", default=sorted(VEHICLE_LABELS), help="COCO classes treated as vehicles")
    g.add_argument("--no-track", action="store_true", help="detection only, no ByteTrack IDs")
    g.add_argument("--layout", help="floor_layout.json: calibrate live from floor markers")
    g.add_argument("--calib", help="floor_calib.json saved by check_floor / run_vision (fixed calibration)")
    g.add_argument("--save", default="floor_calib.json")
    g.add_argument("--head-yaw", type=float, default=0.0)
    g.add_argument("--print-every", type=float, default=1.0, help="console summary interval (s), 0 = off")
    g.add_argument("--no-window", action="store_true", help="no OpenCV overlay window")
    g.add_argument("--mqtt", metavar="HOST[:PORT]", help="also publish each message on sightline/vision/hazards")
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
    det = Detector(args.model, args.conf, args.imgsz, not args.no_track, args.vehicles,
                   vehicle_conf=args.vehicle_conf)
    tags = {i: args.car_label for i in args.car_marker}
    pipe = VisionPipeline(det, layout, fm, args.head_yaw, hazard_markers=tags)
    hub = HazardHub(args.mqtt)
    model = PhoneHazardModel(pipe, window=not args.no_window, save_path=args.save, layout_path=args.layout,
                             print_every_s=args.print_every, hub=hub)

    ip = server.lan_ip()
    ssl_ctx = None
    if not args.no_https:
        cert, key = STREAM_DIR / "cert.pem", STREAM_DIR / "key.pem"
        server.ensure_cert(cert, key, ip, args.new_cert)
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(cert, key)
    scheme = "http" if args.no_https else "https"
    print(f"\nmodel: {model.name}")
    print(f"floor: {'saved calibration ' + args.calib if fm else 'live from ' + args.layout if layout else 'none (pixels only)'}")
    print(f"open on the phone (same Wi-Fi):  {scheme}://{ip}:{args.port}")
    print(f"raw stream + boxes on this PC:   {scheme}://localhost:{args.port}/view")
    print(f"live angles (SSE):               {scheme}://localhost:{args.port}/hazards")
    print(f"vehicles: YOLO {sorted(det.vehicle_labels)} >= {det.vehicle_conf}"
          + (f", tagged {args.car_label} = marker {args.car_marker}" if tags else ""))
    if args.mqtt:
        print(f"mqtt: {HazardHub.TOPIC} on {args.mqtt}")
    print(f"status:                          {scheme}://localhost:{args.port}/status\n")
    app = server.create_app(model, args.view_fps)
    hub.attach(app)
    try:
        web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_ctx, print=None)
    finally:
        hub.close()


if __name__ == "__main__":
    main()
