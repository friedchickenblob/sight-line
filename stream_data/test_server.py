"""End to end without a phone: an aiortc client plays the phone's part.

    python -m pytest test_server.py
"""
import asyncio
import json
import threading
import time

import numpy as np
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

from model import NullModel, load_model
from server import LatestFrameWorker, create_app


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
    await pc.close()
    return got, status, page


def test_phone_stream_reaches_the_model_and_results_come_back():
    async def run():
        server = TestServer(create_app(NullModel()))
        await server.start_server()
        try:
            return await _phone(str(server.make_url("")).rstrip("/"), seconds=8.0)
        finally:
            await server.close()

    got, status, page = asyncio.run(run())
    assert "SightLine" in page
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
