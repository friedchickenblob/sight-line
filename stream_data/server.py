"""Phone camera -> WebRTC -> computer vision model.

    python server.py                    # https://<this-computer's-IP>:8443, open it in Chrome on the phone
    python server.py --model yolo       # run YOLO on the frames (pip install ultralytics)

Watch the live video on the computer at https://localhost:8443/view (every phone,
with the model's boxes drawn on top when it returns "detections").

The phone page (static/index.html) sends its camera as a WebRTC video track. Every
frame that arrives is stamped with pylsl.local_clock() (the team's shared clock)
and handed to the model in a worker thread. The worker always takes the NEWEST
frame and drops older ones, so a slow model adds no queueing lag. Model results
go back to the phone over a WebRTC data channel and are printed once a second.

HTTPS is required: Chrome only gives a page the camera on a secure origin. A
self-signed certificate is generated on first run (cert.pem / key.pem, git-ignored).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import ipaddress
import json
import logging
import socket
import ssl
import threading
import time
from io import BytesIO
from pathlib import Path

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription

from model import load_model

HERE = Path(__file__).resolve().parent
log = logging.getLogger("stream")
MODEL = web.AppKey("model", object)
SESSIONS = web.AppKey("sessions", set)
VIEW_FPS = web.AppKey("view_fps", float)

try:
    from pylsl import local_clock as clock          # shared clock (CLAUDE.md), so frames line up with the Muse
except Exception:                                    # pylsl missing or its DLL not found
    clock = time.monotonic
    log.warning("pylsl not available: timestamps use time.monotonic(), not local_clock()")


# ----------------------------------------------------------------- model worker
class LatestFrameWorker:
    """Runs the model on the newest frame only, in its own thread.

    submit() just replaces the pending frame, so the event loop never waits for
    the model, and frames the model is too slow for are counted as dropped.
    """

    def __init__(self, model, on_result):
        self.model, self.on_result = model, on_result
        self.frames_in = self.frames_done = self.frames_dropped = 0
        self.last_infer_ms = 0.0
        self._pending = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, frame, t_s: float):
        with self._lock:
            if self._pending is not None:
                self.frames_dropped += 1
            self._pending = (frame, t_s)
            self.frames_in += 1
        self._wake.set()

    def _run(self):
        while not self._stop.is_set():
            if not self._wake.wait(0.5):
                continue
            with self._lock:
                job, self._pending = self._pending, None
                self._wake.clear()
            if job is None:
                continue
            frame, t_s = job
            img = frame.to_ndarray(format="bgr24")    # H x W x 3 uint8, OpenCV channel order
            t0 = time.perf_counter()
            try:
                out = self.model(img, t_s)
            except Exception as e:                    # keep streaming; report the error to the phone
                out = {"error": f"{type(e).__name__}: {e}"}
                log.exception("model failed")
            self.last_infer_ms = (time.perf_counter() - t0) * 1000
            self.frames_done += 1
            self.on_result({"v": 1, "t_s": t_s, "latency_s": clock() - t_s, "infer_ms": self.last_infer_ms,
                            "width": img.shape[1], "height": img.shape[0], "frames_in": self.frames_in,
                            "frames_dropped": self.frames_dropped, "result": out})

    def stop(self):
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=2)


# --------------------------------------------------------------------- sessions
class Session:
    """One phone: its peer connection, data channel and model worker."""

    count = 0

    def __init__(self, model, loop):
        Session.count += 1
        self.id = Session.count
        self.pc = RTCPeerConnection()                  # same network: host candidates, no STUN/TURN needed
        self.channel = None
        self.latest: dict | None = None
        self.loop = loop
        self.worker = LatestFrameWorker(model, self._result)
        self.started = clock()
        self.closed = False
        self.frame = None                              # newest frame, for the /view video
        self.frame_seq = 0

    def _result(self, msg: dict):                     # called from the worker thread
        self.latest = msg
        self.loop.call_soon_threadsafe(self._send, msg)

    def _send(self, msg: dict):
        if self.channel is not None and self.channel.readyState == "open":
            self.channel.send(json.dumps(msg, default=float))

    async def consume(self, track):
        try:
            while True:
                frame = await track.recv()
                self.frame, self.frame_seq = frame, self.frame_seq + 1
                self.worker.submit(frame, clock())
        except Exception:                             # MediaStreamError when the phone stops
            pass

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.worker.stop()
        await self.pc.close()

    def status(self) -> dict:
        w = self.worker
        return dict(id=self.id, state=self.pc.connectionState, seconds=round(clock() - self.started, 1),
                    frames_in=w.frames_in, frames_done=w.frames_done, frames_dropped=w.frames_dropped,
                    infer_ms=round(w.last_infer_ms, 1),
                    latest=None if self.latest is None else self.latest["result"])


# ----------------------------------------------------------------- viewer
VIEW_MAX_WIDTH = 1280


def render_jpeg(frame, result: dict | None, quality: int = 80) -> bytes:
    """One frame as JPEG for the /view page, with the model's detections drawn on it.

    Boxes come from the newest model result, which may be a frame or two older
    than the picture when the model is slower than the camera."""
    from PIL import ImageDraw

    img = frame.to_image()                            # PIL RGB
    scale = 1.0
    if img.width > VIEW_MAX_WIDTH:
        scale = VIEW_MAX_WIDTH / img.width
        img = img.resize((VIEW_MAX_WIDTH, round(img.height * scale)))
    dets = (result or {}).get("detections") if isinstance(result, dict) else None
    if dets:
        draw = ImageDraw.Draw(img)
        for d in dets:
            try:
                x0, y0, x1, y1 = (float(v) * scale for v in d["xyxy"])
            except (KeyError, TypeError, ValueError):
                continue
            draw.rectangle((x0, y0, x1, y1), outline=(255, 64, 64), width=3)
            label = f"{d.get('label', '')} {d.get('conf', '')}".strip()
            if label:
                tw = draw.textlength(label)
                draw.rectangle((x0, max(y0 - 14, 0), x0 + tw + 6, max(y0, 14)), fill=(255, 64, 64))
                draw.text((x0 + 3, max(y0 - 13, 1)), label, fill=(255, 255, 255))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


# ------------------------------------------------------------------------- app
def create_app(model, view_fps: float = 15.0) -> web.Application:
    app = web.Application()
    app[MODEL] = model
    app[SESSIONS] = set()
    app[VIEW_FPS] = view_fps

    async def index(request):
        return web.FileResponse(HERE / "static" / "index.html")

    async def offer(request):
        params = await request.json()
        s = Session(request.app[MODEL], asyncio.get_running_loop())
        request.app[SESSIONS].add(s)
        log.info("phone %d connecting from %s", s.id, request.remote)

        @s.pc.on("datachannel")
        def on_datachannel(channel):
            s.channel = channel

        @s.pc.on("track")
        def on_track(track):
            if track.kind == "video":
                asyncio.ensure_future(s.consume(track))

        @s.pc.on("connectionstatechange")
        async def on_state():
            log.info("phone %d: %s", s.id, s.pc.connectionState)
            if s.pc.connectionState in ("failed", "closed"):
                await s.close()
                request.app[SESSIONS].discard(s)

        await s.pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
        await s.pc.setLocalDescription(await s.pc.createAnswer())   # aiortc gathers candidates here
        return web.json_response({"sdp": s.pc.localDescription.sdp, "type": s.pc.localDescription.type})

    async def status(request):
        return web.json_response({"model": getattr(request.app[MODEL], "name", "custom"),
                                  "sessions": [s.status() for s in request.app[SESSIONS]]})

    async def view(request):
        return web.FileResponse(HERE / "static" / "view.html")

    async def video(request):
        """MJPEG: the phone's newest frame, again and again, for an <img> tag."""
        sid = int(request.match_info["id"])
        s = next((x for x in request.app[SESSIONS] if x.id == sid), None)
        if s is None:
            raise web.HTTPNotFound(text=f"no phone {sid}")
        resp = web.StreamResponse(headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame",
                                           "Cache-Control": "no-cache"})
        await resp.prepare(request)
        loop, last, gap = asyncio.get_running_loop(), 0, 1.0 / request.app[VIEW_FPS]
        try:
            while not s.closed:
                if s.frame_seq != last and s.frame is not None:
                    last = s.frame_seq
                    result = s.latest["result"] if s.latest else None
                    jpg = await loop.run_in_executor(None, render_jpeg, s.frame, result)
                    await resp.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                await asyncio.sleep(gap)
        except (ConnectionResetError, asyncio.CancelledError):
            pass                                       # the viewer closed the page
        return resp

    async def on_shutdown(app):
        await asyncio.gather(*(s.close() for s in list(app[SESSIONS])))
        app[SESSIONS].clear()

    async def report(app):
        async def loop():
            last = {}
            while True:
                await asyncio.sleep(1.0)
                for s in list(app[SESSIONS]):
                    w, prev = s.worker, last.get(s.id, (0, 0))
                    if w.frames_in == prev[0]:
                        continue
                    log.info("phone %d: %d fps in, %d fps model (%.0f ms), %d dropped, latest %s",
                             s.id, w.frames_in - prev[0], w.frames_done - prev[1], w.last_infer_ms,
                             w.frames_dropped, json.dumps(s.latest["result"] if s.latest else None)[:120])
                    last[s.id] = (w.frames_in, w.frames_done)
        task = asyncio.ensure_future(loop())
        yield
        task.cancel()

    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    app.router.add_get("/status", status)
    app.router.add_get("/view", view)
    app.router.add_get(r"/video/{id:\d+}.mjpg", video)
    app.router.add_static("/static/", HERE / "static")
    app.on_shutdown.append(on_shutdown)
    app.cleanup_ctx.append(report)
    return app


# ------------------------------------------------------------------- HTTPS
def lan_ip() -> str:
    """This computer's address on the local network (no packet is actually sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def ensure_cert(cert: Path, key: Path, ip: str, renew: bool = False):
    """Self-signed certificate for localhost and this LAN IP (Chrome needs HTTPS for the camera)."""
    if cert.exists() and key.exists() and not renew:
        return
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SightLine stream_data")])
    now = datetime.datetime.now(datetime.timezone.utc)
    san = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    if ip != "127.0.0.1":
        san.append(x509.IPAddress(ipaddress.ip_address(ip)))
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(pk.public_key())
         .serial_number(x509.random_serial_number())
         .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=365))
         .add_extension(x509.SubjectAlternativeName(san), critical=False)
         .sign(pk, hashes.SHA256()))
    key.write_bytes(pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                     serialization.NoEncryption()))
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    log.info("created self-signed certificate %s for %s", cert.name, ip)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0", help="0.0.0.0 = reachable from the phone on the same network")
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--model", default="null", help="null | yolo | yolo:<weights.pt> | <module>:<callable>")
    ap.add_argument("--view-fps", type=float, default=15.0, help="frame rate of the /view video on the computer")
    ap.add_argument("--no-https", action="store_true", help="plain HTTP (camera only works on http://localhost)")
    ap.add_argument("--new-cert", action="store_true", help="regenerate cert.pem / key.pem (e.g. new IP)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    for noisy in ("aioice", "aiortc", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    model = load_model(args.model)
    ip = lan_ip()
    ssl_ctx = None
    if not args.no_https:
        cert, key = HERE / "cert.pem", HERE / "key.pem"
        ensure_cert(cert, key, ip, args.new_cert)
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(cert, key)
    scheme = "http" if args.no_https else "https"
    print(f"\nmodel: {getattr(model, 'name', args.model)}")
    print(f"open on the phone (same Wi-Fi):  {scheme}://{ip}:{args.port}")
    print(f"watch the video on this computer: {scheme}://localhost:{args.port}/view")
    print(f"status:                          {scheme}://localhost:{args.port}/status\n")
    web.run_app(create_app(model, args.view_fps), host=args.host, port=args.port, ssl_context=ssl_ctx, print=None)


if __name__ == "__main__":
    main()
