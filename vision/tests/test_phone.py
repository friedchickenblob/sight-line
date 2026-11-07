"""The phone-stream model: frames in, hazard messages out (no phone, no YOLO), the toy-car
marker tag, and the /hazards live stream."""

import asyncio
import json

import numpy as np
import pytest

from test_floor import LAYOUT, render
from test_scene import fake
from vision.detector import Detection
from vision.floor import calibrate
from vision.pipeline import VisionPipeline, add_marker_hazards
from vision.run_phone import HazardHub, PhoneHazardModel, import_stream_server


class FakeDetector:
    weights = "fake.pt"

    def __init__(self, dets):
        self.dets = dets

    def __call__(self, frame):
        return self.dets


WORKER = fake("person", (1.0, 1.0), track_id=1)
DETS = [WORKER, fake("vehicle", (2.0, 2.0), track_id=7)]


def model_for(dets, **pipe_kw):
    return PhoneHazardModel(VisionPipeline(FakeDetector(dets), **pipe_kw), window=False, print_every_s=0)


def test_live_calibration_gives_angles_and_json():
    out = model_for(DETS, layout=LAYOUT, head_yaw_deg=90)(render(), 12.5)
    json.dumps(out)                                          # the server sends it as JSON
    assert out["v"] == 1 and out["t_s"] == 12.5 and out["frame"] == 1
    assert out["calib"] == "live calib, all markers"
    w = out["worker"]
    assert w["track_id"] == 1 and (w["x_m"], w["y_m"]) == pytest.approx((1.0, 1.0), abs=0.03)
    (h,) = out["hazards"]
    assert h["label"] == "car" and h["source"] == "yolo"
    assert h["distance_m"] == pytest.approx(2 ** 0.5, abs=0.04)
    assert h["bearing_deg"] == pytest.approx(45, abs=2)
    assert h["rel_bearing_deg"] == pytest.approx(-45, abs=2)  # head faces +y, car is ahead-right
    assert [d["label"].split()[0] for d in out["detections"]] == ["WORKER", "car"]   # /view boxes
    assert all(len(d["xyxy"]) == 4 for d in out["detections"])


def test_without_markers_it_still_reports_pixels():
    out = model_for(DETS, layout=LAYOUT)(np.full((720, 1280, 3), 200, np.uint8), 0.0)
    assert out["calib"].startswith("live calib: markers not seen")
    assert out["worker"]["x_m"] is None and out["hazards"][0]["distance_m"] is None


def test_frozen_saved_calibration_is_used_as_is():
    fm = calibrate(render(), LAYOUT)[0]
    out = model_for(DETS, floor_map=fm)(render(), 0.0)
    assert out["calib"] == "calibration fixed" and out["hazards"][0]["bearing_deg"] == pytest.approx(45, abs=2)


def test_toy_car_found_by_its_marker_tag():
    """No YOLO car at all: the tag (marker 4) alone gives the car's position and angle."""
    frame = render(extra={4: (2.5, 0.5)})
    out = model_for([WORKER], layout=LAYOUT, hazard_markers={4: "toy car"}, head_yaw_deg=0)(frame, 0.0)
    (h,) = out["hazards"]
    assert h["label"] == "toy car" and h["source"] == "marker" and h["track_id"] == 4
    assert (h["x_m"], h["y_m"]) == pytest.approx((2.5, 0.5), abs=0.03)
    assert h["distance_m"] == pytest.approx(np.hypot(1.5, -0.5), abs=0.04)
    assert h["bearing_deg"] == pytest.approx(np.degrees(np.arctan2(-0.5, 1.5)), abs=2)   # ~ -18 deg
    assert h["rel_bearing_deg"] == pytest.approx(h["bearing_deg"], abs=0.1)              # head along +x


def test_tag_replaces_the_yolo_box_around_it():
    tag_corners = np.array([[100, 100], [140, 100], [140, 140], [100, 140]], float)
    yolo_car = Detection("vehicle", "car", 0.4, (80, 80, 200, 170), 9)
    other_car = Detection("vehicle", "truck", 0.8, (400, 400, 500, 480), 3)
    out = add_marker_hazards([WORKER, yolo_car, other_car], {4: tag_corners}, {4: "toy car"})
    assert [d.label for d in out] == ["person", "truck", "toy car"]
    assert out[-1].ground_px == (120.0, 120.0)            # tag centre, not the box bottom


def test_tag_id_must_not_be_a_floor_marker():
    with pytest.raises(ValueError):
        VisionPipeline(FakeDetector([]), layout=LAYOUT, hazard_markers={2: "toy car"})


def test_hazards_stream_sends_each_message():
    from aiohttp import ClientSession
    from aiohttp.test_utils import TestServer

    server = import_stream_server()
    hub = HazardHub()
    model = PhoneHazardModel(VisionPipeline(FakeDetector(DETS), layout=LAYOUT), window=False,
                             print_every_s=0, hub=hub)
    app = server.create_app(model)
    hub.attach(app)
    frame = render()

    async def run():
        ts = TestServer(app)
        await ts.start_server()
        try:
            async with ClientSession() as http, http.get(ts.make_url("/hazards")) as r:
                assert r.headers["Content-Type"].startswith("text/event-stream")
                for _ in range(50):                          # wait until the handler has subscribed
                    if hub.queues:
                        break
                    await asyncio.sleep(0.02)
                await asyncio.get_running_loop().run_in_executor(None, model, frame, 3.0)
                line = await asyncio.wait_for(r.content.readline(), 5)
                return json.loads(line.decode().removeprefix("data: "))
        finally:
            await ts.close()

    msg = asyncio.run(run())
    assert msg["v"] == 1 and msg["t_s"] == 3.0 and "detections" not in msg
    assert msg["hazards"][0]["bearing_deg"] == pytest.approx(45, abs=2)
