"""Live dashboard: every recorded field on screen, with recording controls.

    python dashboard/app.py                               # Muse S over BrainFlow (page works without it)
    python dashboard/app.py --board synthetic             # BrainFlow's fake board, no headband
    python dashboard/app.py --source synthetic:joint      # synthetic recording, played in real time
    python dashboard/app.py --source replay:data/raw/session.csv
    python dashboard/app.py --phone                       # also stream a phone camera (stream_data/)
    python dashboard/app.py --phone --phone-model yolo    # ... with YOLO boxes on the video

Then open http://127.0.0.1:5000. Start/Stop writes a CSV to data/raw/ in the same
format as record.py (kind,t,v0,v1,v2), so analyze.py and replay read it as is.
Mark and Re-zero add MARK / RESET rows, like pressing Enter in record.py / live_yaw.py.

With the Muse, the page comes up straight away, connected or not: it searches for the
band once at startup, and the "Search for Muse" button searches again (in the background).

The browser gets updates ~10 times a second over Server-Sent Events (/stream).

With --phone the phone-camera server from stream_data/ runs in this same process, on its
own port (8443, HTTPS, because the phone's browser needs it for the camera). Open the
link the dashboard prints on the phone. The dashboard shows the phone's video and model
output in a "Phone camera" card; it fetches them in-process (/api/phone and
/phone/video/<id>.mjpg), so the dashboard page stays plain http://127.0.0.1:5000.
Frames and Muse samples are both stamped with pylsl.local_clock().
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import threading
import time
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flask import Flask, Response, jsonify, render_template, request  # noqa: E402

from headmotion.calibration import Calibration      # noqa: E402
from headmotion.health import StreamHealth          # noqa: E402
from headmotion.sources import DROP, MARK, RESET, CsvRecorder, Sample  # noqa: E402
from headmotion.yaw import HeadYawEstimator, YawPipeline  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"
PHONE_DIR = ROOT.parent / "stream_data"
TICK_S = 0.1


def _row(s: Sample) -> list:
    """A sample as the CSV writes it: kind, t, v0, v1, v2 (missing values are null)."""
    v = [float(x) for x in s.v] + [None] * (3 - len(s.v))
    return [s.kind, float(s.t), *v[:3]]


class Hub:
    """Owns the source loop and everything the page shows.

    One background thread reads the source, feeds the yaw pipeline and stream
    health, writes the recording, and appends every sample to a ring buffer.
    Each browser keeps a cursor into that buffer, so it gets every row once.
    """

    def __init__(self, source, pipeline: YawPipeline, raw_dir: Path = RAW_DIR, keep: int = 20000,
                 source_name: str = "", publisher=None):
        self.src, self.pipe, self.raw_dir = source, pipeline, Path(raw_dir)
        self.source_name, self.pub = source_name, publisher
        self.health = StreamHealth()
        self.lock = threading.Lock()
        self.rows: deque = deque(maxlen=keep)        # (seq, row)
        self.yaws: deque = deque(maxlen=keep // 2)   # (seq, t, yaw_deg): estimate per gyro sample, for the chart
        self.log: deque = deque(maxlen=500)          # (seq, t, text)
        self.seq = self.log_seq = 0
        self.latest: dict = {}                       # kind -> row
        self.counts: Counter = Counter()
        self.yaw = None
        self.rec: CsvRecorder | None = None
        self.rec_t0: float | None = None
        self._pending: list = []                     # MARK / RESET from the buttons
        self._marks = 0
        self._shown = 0
        self.ended = False
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_pub = float("-inf")

    # -- source loop
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        with self.lock:
            self._close_recording()
        if hasattr(self.src, "close"):
            self.src.close()

    def _run(self):
        try:
            for batch in self.src.batches():
                if self._stop.is_set():
                    break
                self.step(batch)
        except Exception as e:                       # show it on the page instead of dying silently
            with self.lock:
                self.error = f"{type(e).__name__}: {e}"
                self._note(self.error)
        with self.lock:
            self.ended = True
            self._note("source ended")

    def step(self, batch):
        """Process one batch (the loop calls this; tests can too)."""
        with self.lock:
            now = self.src.now()
            pending, self._pending = self._pending, []
            for kind, v in pending:
                if kind == RESET and not self.pipe.running:
                    self._note("re-zero ignored: still measuring bias")
                    continue
                batch = batch + [Sample(kind, now, v)]
            for s in batch:
                self._process(s)
            for e in self.pipe.events[self._shown:]:
                self._note(e)
            self._shown = len(self.pipe.events)
            events = getattr(self.src, "events", None)
            while events:                              # popleft: a search thread may append meanwhile
                self._note(events.popleft())
            if self.pub and now >= self._next_pub:
                self._next_pub = now + 1.0
                self.pub.publish(self.health.report(now, getattr(self.src, "battery_pct", None)))

    def _process(self, s: Sample):
        self.health.feed(s)
        out = self.pipe.feed(s)
        if self.rec:
            self.rec.write(s)
        row = _row(s)
        self.seq += 1
        self.rows.append((self.seq, row))
        if out is not None:
            self.yaw = out
            if out.yaw_deg is not None:
                self.yaws.append((self.seq, out.t_s, out.yaw_deg))
            if self.pub:
                self.pub.publish(out)
        self.latest[s.kind] = row
        self.counts[s.kind] += 1
        if s.kind == DROP:
            self._note(f"lost {int(s.v[0])} samples")
        elif s.kind == MARK:
            self._note(f"MARK {int(s.v[0])}")
        elif s.kind == RESET:
            self._note("re-zeroed")

    def _note(self, text: str):
        self.log_seq += 1
        self.log.append((self.log_seq, self.src.now(), text))

    # -- buttons
    def start_recording(self, name: str = "") -> dict:
        with self.lock:
            if self.rec:
                raise RuntimeError(f"already recording to {self.rec.path.name}")
            name = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()) or datetime.now().strftime("session_%Y%m%d_%H%M%S")
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            path = self.raw_dir / f"{name}.csv"
            if path.exists():
                raise RuntimeError(f"{path.name} already exists: pick another name")
            self.rec = CsvRecorder(path).__enter__()
            self.rec_t0 = self.src.now()
            self._marks = 0
            self._note(f"recording to {path.name}")
            return self._rec_state()

    def stop_recording(self) -> dict:
        with self.lock:
            if not self.rec:
                raise RuntimeError("not recording")
            state = self._rec_state() | dict(on=False)     # what was saved, now stopped
            self._close_recording()
            return state

    def _close_recording(self):
        if self.rec:
            self.rec.__exit__(None, None, None)
            self._note(f"saved {self.rec.count} rows to {self.rec.path.name}")
            self.rec, self.rec_t0 = None, None

    def mark(self):
        with self.lock:
            self._pending.append((MARK, (float(self._marks),)))
            self._marks += 1

    def rezero(self):
        with self.lock:
            self._pending.append((RESET, (0.0,)))

    def search_muse(self) -> dict:
        """Look for the Muse in a background thread (connecting blocks for up to ~15 s)."""
        if not hasattr(self.src, "search"):
            raise RuntimeError("this source has no Muse to search for")
        st = self._muse_state()
        if not st["connected"] and not st["searching"]:
            self.src.searching = True                   # show "searching" before the thread gets going
            threading.Thread(target=self.src.search, daemon=True, name="muse-search").start()
        return self._muse_state()

    def _muse_state(self) -> dict:
        if not hasattr(self.src, "search"):
            return dict(available=False, connected=None, searching=False)
        return dict(available=True, connected=self.src.connected, searching=self.src.searching)

    # -- what the page sees
    def _rec_state(self) -> dict:
        if not self.rec:
            return dict(on=False)
        return dict(on=True, file=str(self.rec.path), name=self.rec.path.name, rows=self.rec.count,
                    seconds=self.src.now() - self.rec_t0)

    def snapshot(self, cursor: int = 0, log_cursor: int = 0) -> dict:
        with self.lock:
            now = self.src.now()
            h = self.health.report(now, getattr(self.src, "battery_pct", None))
            return dict(
                now=now, source=self.source_name, ended=self.ended, error=self.error,
                seq=self.seq, log_seq=self.log_seq,
                rows=[r for k, r in self.rows if k > cursor],
                yaws=[[t, y] for k, t, y in self.yaws if k > cursor],
                log=[[t, text] for k, t, text in self.log if k > log_cursor],
                latest=self.latest, counts=dict(self.counts),
                yaw=None if self.yaw is None else self.yaw.as_dict(),
                health=h.as_dict() | dict(total_drops=self.health.total_drops,
                                          lost_samples=self.health.lost_samples),
                bias=None if self.pipe.est.bias is None else [round(float(b), 3) for b in self.pipe.est.bias],
                recording=self._rec_state(),
                muse=self._muse_state(),
            )


def create_app(hub: Hub, phone=None) -> Flask:
    """phone: a running stream_data PhoneStream (--phone), or None."""
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/state")
    def state():
        return jsonify(hub.snapshot(hub.seq, hub.log_seq) | dict(rows=[], yaws=[], log=[]))

    @app.get("/stream")
    def stream():
        def events():
            cursor, log_cursor = max(hub.seq - 2000, 0), 0     # start with the last ~20 s of samples
            while True:
                snap = hub.snapshot(cursor, log_cursor)
                cursor, log_cursor = snap["seq"], snap["log_seq"]
                yield f"data: {json.dumps(snap)}\n\n"
                time.sleep(TICK_S)
        return Response(events(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})

    def action(fn):
        try:
            return jsonify(ok=True, recording=fn())
        except RuntimeError as e:
            return jsonify(ok=False, error=str(e)), 409

    @app.post("/api/record/start")
    def record_start():
        name = (request.get_json(silent=True) or {}).get("name", "")
        return action(lambda: hub.start_recording(name))

    @app.post("/api/record/stop")
    def record_stop():
        return action(hub.stop_recording)

    @app.post("/api/mark")
    def mark():
        hub.mark()
        return jsonify(ok=True)

    @app.post("/api/rezero")
    def rezero():
        hub.rezero()
        return jsonify(ok=True)

    @app.post("/api/muse/search")
    def muse_search():
        try:
            return jsonify(ok=True, muse=hub.search_muse())
        except RuntimeError as e:
            return jsonify(ok=False, error=str(e)), 409

    # -- phone camera (--phone)
    @app.get("/api/phone")
    def phone_status():
        if phone is None:
            return jsonify(enabled=False)
        try:
            st = phone.status()
        except Exception as e:                       # server thread stopped or busy: say so on the page
            return jsonify(enabled=True, url=phone.url, error=f"{type(e).__name__}: {e}", model=None, sessions=[])
        return jsonify(enabled=True, url=phone.url, error=None, **st)

    @app.get("/phone/video/<int:session_id>.mjpg")
    def phone_video(session_id: int):
        frames = phone.mjpeg(session_id) if phone is not None else None
        if frames is None:
            return jsonify(ok=False, error=f"no phone {session_id}"), 404
        return Response(frames, content_type="multipart/x-mixed-replace; boundary=frame",
                        headers={"Cache-Control": "no-cache"})

    return app


def open_phone_stream(args):
    """Start stream_data's phone-camera server in this process (--phone)."""
    if str(PHONE_DIR) not in sys.path:
        sys.path.insert(0, str(PHONE_DIR))            # stream_server does `from model import ...`
    try:
        import stream_server
        from model import load_model
    except ImportError as e:
        raise SystemExit(f"--phone needs the phone-stream packages ({e.name}): "
                         "pip install -r requirements.txt from the repo root") from e
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("aioice", "aiortc", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    model = load_model(args.phone_model)
    ctx = None if args.phone_no_https else stream_server.https_context(stream_server.lan_ip(), args.phone_new_cert)
    try:
        return stream_server.PhoneStream(model, args.phone_host, args.phone_port, ctx, args.phone_view_fps).start()
    except RuntimeError as e:
        raise SystemExit(str(e)) from e


def main():
    from headmotion.cli import add_source_args, camera_subscriber, muse_kwargs
    from headmotion.sources import open_source

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_source_args(ap)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to open it from another device on the network")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--raw-dir", default=str(RAW_DIR), help="where recordings are saved")
    ap.add_argument("--bias-seconds", type=float, default=5.0)
    g = ap.add_argument_group("phone camera (stream_data/)")
    g.add_argument("--phone", action="store_true", help="also run the phone-camera stream and show it on the dashboard")
    g.add_argument("--phone-model", default="null", help="null | yolo | yolo:<weights.pt> | <module>:<callable>")
    g.add_argument("--phone-host", default="0.0.0.0", help="0.0.0.0 = reachable from the phone on the same network")
    g.add_argument("--phone-port", type=int, default=8443)
    g.add_argument("--phone-view-fps", type=float, default=15.0, help="frame rate of the video on the dashboard")
    g.add_argument("--phone-no-https", action="store_true", help="plain HTTP (the phone camera then won't work)")
    g.add_argument("--phone-new-cert", action="store_true", help="regenerate the self-signed certificate (e.g. new IP)")
    args = ap.parse_args()

    live = args.source == "muse"
    cam = camera_subscriber(args)
    muse_kw = muse_kwargs(args, [cam] if cam else []) | dict(connect=False) if live else {}
    src = open_source(args.source, realtime=True, **muse_kw)    # live: page first, Muse search below
    pub = None
    if args.mqtt:
        from headmotion.bus import Publisher, parse_host
        pub = Publisher(*parse_host(args.mqtt))
    cal = Calibration.load()
    pipe = YawPipeline(HeadYawEstimator(cal), args.bias_seconds, start_yaw=0.0, retry_bias=True)
    name = args.source if not live else f"muse ({args.board})"
    hub = Hub(src, pipe, Path(args.raw_dir), source_name=name, publisher=pub)
    if cal.pending():
        hub._note("calibration placeholders: " + ", ".join(cal.pending()))
    hub._note(f"hold still for {args.bias_seconds:.0f} s, facing the start line (bias)")
    phone = open_phone_stream(args) if args.phone else None
    hub.start()
    if live:
        hub.search_muse()                               # first search in the background; button retries
    print(f"dashboard: http://{args.host}:{args.port}")
    if phone is not None:
        hub._note(f"phone camera: open {phone.url} on the phone (same Wi-Fi)")
        print(f"phone camera: open {phone.url} on the phone (same Wi-Fi), model: {getattr(phone.model, 'name', '?')}")
    try:
        create_app(hub, phone).run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    finally:
        hub.stop()
        for x in (phone, pub, cam):
            if x is not None:
                x.close()


if __name__ == "__main__":
    main()
