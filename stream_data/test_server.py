"""End to end without a phone: an aiortc client plays the phone's part.

    python -m pytest test_server.py
"""
import asyncio
import json
import threading
import time
from io import BytesIO

import numpy as np
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

from model import NullModel, load_model
from server import LatestFrameWorker, create_app, render_jpeg


class FakeCamera(VideoStreamTrack):
    """A 640x480 grey picture at 30 fps, like a phone camera pointed at a wall."""

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        frame = VideoFrame.from_ndarray(np.full((480, 640, 3), 120, np.uint8), format="bgr24")
        frame.pts, frame.time_base = pts, time_base
        return frame


async def _phone(base_url: str, seconds: float = 3.0):
    pc = RTCPeerConnection()
    pc.addTrack(FakeCamera())
    channel = pc.createDataChannel("results")
    got = []
    channel.on("message", lambda m: got.append(json.loads(m)))
    await pc.setLocalDescription(await pc.createOffer())
    async with ClientSession() as http:
        async with http.post(f"{base_url}/offer", json={"sdp": pc.localDescription.sdp,
                                                         "type": pc.localDescription.type}) as r:
            answer = await r.json()
        await pc.setRemoteDescription(RTCSessionDescription(**answer))
        t_end = time.monotonic() + seconds
        while time.monotonic() < t_end and len(got) < 20:
            await asyncio.sleep(0.1)
        async with http.get(f"{base_url}/status") as r:
            status = await r.json()
        async with http.get(f"{base_url}/") as r:
            page = await r.text()
        async with http.get(f"{base_url}/view") as r:
            page += await r.text()
        sid = status["sessions"][0]["id"]
        async with http.get(f"{base_url}/video/{sid}.mjpg") as r:
            mime = r.headers["Content-Type"]
            first = await r.content.readuntil(b"\xff\xd9")           # end of the first JPEG
    await pc.close()
    return got, status, page, (mime, first)


def test_phone_stream_reaches_the_model_and_results_come_back():
    async def run():
        server = TestServer(create_app(NullModel()))
        await server.start_server()
        try:
            return await _phone(str(server.make_url("")).rstrip("/"), seconds=8.0)
        finally:
            await server.close()

    got, status, page, (mime, first) = asyncio.run(run())
    assert "SightLine · phone camera" in page and "SightLine · live phone video" in page
    assert mime.startswith("multipart/x-mixed-replace")
    jpg = first[first.index(b"\xff\xd8"):]
    from PIL import Image
    assert Image.open(BytesIO(jpg)).size == (640, 480)       # the phone's live video, viewable in a browser
    assert len(got) >= 5, "no model results came back over the data channel"
    m = got[-1]
    assert m["v"] == 1 and m["result"]["shape"] == [480, 640, 3]
    assert m["result"]["mean_brightness"] > 100        # the grey picture, after video compression
    assert 0 <= m["latency_s"] < 1.0
    assert status["model"] == "null" and status["sessions"][0]["frames_in"] >= 5


def test_worker_drops_old_frames_when_the_model_is_slow():
    class Frame:
        def to_ndarray(self, format):
            return np.zeros((4, 4, 3), np.uint8)

    seen, done = [], threading.Event()

    def slow_model(img, t):
        time.sleep(0.05)
        seen.append(t)
        return {}

    w = LatestFrameWorker(slow_model, lambda m: done.set() if m["t_s"] == 99 else None)
    for t in range(100):
        w.submit(Frame(), t)
    assert done.wait(2.0)                                # the newest frame always gets processed
    w.stop()
    assert seen[-1] == 99 and len(seen) < 10 and w.frames_dropped > 90


def test_model_errors_are_reported_not_fatal():
    class Frame:
        def to_ndarray(self, format):
            return np.zeros((4, 4, 3), np.uint8)

    out, done = [], threading.Event()
    w = LatestFrameWorker(lambda img, t: 1 / 0, lambda m: (out.append(m), done.set()))
    w.submit(Frame(), 1.0)
    assert done.wait(2.0)
    w.stop()
    assert "ZeroDivisionError" in out[0]["result"]["error"]


def test_load_model_specs():
    assert load_model("null").name == "null"
    assert callable(load_model("model:NullModel"))


def test_render_jpeg_draws_detections():
    from PIL import Image
    frame = VideoFrame.from_ndarray(np.zeros((480, 640, 3), np.uint8), format="bgr24")
    plain = Image.open(BytesIO(render_jpeg(frame, {"shape": [480, 640, 3]}))).convert("RGB")
    boxed = Image.open(BytesIO(render_jpeg(frame, {"detections": [
        {"label": "person", "conf": 0.9, "xyxy": [100, 100, 300, 400]}, {"bad": "entry"}]}))).convert("RGB")
    assert plain.getpixel((100, 250))[0] < 30                   # black without detections
    r, g, b = boxed.getpixel((100, 250))                         # on the box's left edge
    assert r > 180 and g < 120 and b < 120


def test_render_jpeg_scales_big_frames_and_their_boxes():
    from PIL import Image
    frame = VideoFrame.from_ndarray(np.zeros((1080, 1920, 3), np.uint8), format="bgr24")
    img = Image.open(BytesIO(render_jpeg(frame, {"detections": [{"xyxy": [960, 540, 1500, 900]}]}))).convert("RGB")
    assert img.size == (1280, 720)
    assert img.getpixel((640, 450))[0] > 180                    # 960 x (1280/1920) = 640: box edge moved with the scale
