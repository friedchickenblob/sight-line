"""Track velocities (vision.tracks) and the dark-blob fallback for a tagged vehicle (vision.blob)."""

import numpy as np
import pytest

from vision.blob import BlobFallback, dark_blobs
from vision.tracks import TrackVelocity


def test_velocity_of_steady_motion():
    tv = TrackVelocity()
    v = None
    for i in range(30):                              # 1 s at 30 fps, 0.6 m/s along +x, 0.2 m/s along -y
        t = i / 30
        v = tv.update("car", t, (0.6 * t, 1.0 - 0.2 * t))
    assert v == pytest.approx((0.6, -0.2), abs=1e-6)


def test_velocity_smooths_jitter():
    rng = np.random.default_rng(0)
    tv = TrackVelocity()
    for i in range(30):
        t = i / 30
        v = tv.update("car", t, (0.5 * t + rng.normal(0, 0.01), rng.normal(0, 0.01)))   # 1 cm jitter
    assert v[0] == pytest.approx(0.5, abs=0.1) and abs(v[1]) < 0.1


def test_velocity_waits_for_history_and_restarts_after_a_gap():
    tv = TrackVelocity()
    assert tv.update("car", 0.0, (0, 0)) is None     # one point: no velocity yet
    for i in range(1, 15):
        tv.update("car", i / 30, (i / 30, 0))
    assert tv.update("car", 5.0, (3.0, 0)) is None   # unseen for 4.5 s: starts again, no jump-velocity


def test_tracks_are_separate():
    tv = TrackVelocity()
    for i in range(15):
        t = i / 30
        a = tv.update(("hazard", 20), t, (t, 0))
        b = tv.update(("worker", 3), t, (0, 0))
    assert a[0] == pytest.approx(1.0) and b == pytest.approx((0.0, 0.0))


def floor_with_chair(cx, cy, w=1280, h=720, size=200):
    """Brown floor, black square 'chair' centred on (cx, cy)."""
    img = np.full((h, w, 3), (40, 80, 140), np.uint8)      # BGR brown, V = 140
    img[cy - size // 2:cy + size // 2, cx - size // 2:cx + size // 2] = 10
    return img


def tag(cx, cy, half=10):
    return np.array([[cx - half, cy - half], [cx + half, cy - half], [cx + half, cy + half], [cx - half, cy + half]], float)


def test_dark_blobs_finds_the_chair_only():
    (b,) = dark_blobs(floor_with_chair(400, 300))
    assert b[1] == pytest.approx((400, 300), abs=2)
    small = np.full((720, 1280, 3), (40, 80, 140), np.uint8)
    small[100:120, 100:120] = 0                             # a dark speck is not a machine
    assert dark_blobs(small) == []


def test_blob_keeps_the_vehicle_while_its_tag_is_unreadable():
    fb = BlobFallback()
    labels = {20: "chair"}
    assert fb.update(0.0, floor_with_chair(400, 300), {20: tag(420, 290)}, labels) == []   # tag seen: nothing to add
    (d,) = fb.update(0.1, floor_with_chair(450, 300), {}, labels)                          # tag lost, chair moved 50 px
    assert d.track_id == 20 and d.source == "blob" and d.kind == "vehicle"
    assert d.ground_px == pytest.approx((470, 290), abs=2)                                  # blob + the tag's offset


def test_blob_ignores_a_far_blob_until_relock_and_stops_after_max_gap():
    fb = BlobFallback(relock_s=1.0, max_gap_s=5.0)
    labels = {20: "chair"}
    fb.update(0.0, floor_with_chair(300, 300), {20: tag(300, 300)}, labels)
    far = floor_with_chair(1000, 500)                      # carried to the other side
    assert fb.update(0.5, far, {}, labels) == []           # too far to be the same chair yet
    (d,) = fb.update(1.5, far, {}, labels)                 # lost > relock_s: take the chair-sized blob
    assert d.ground_px == pytest.approx((1000, 500), abs=2)
    assert fb.update(6.0, far, {}, labels) == []           # no tag for > max_gap_s: stop guessing


def test_blob_never_invents_an_untagged_vehicle():
    assert BlobFallback().update(0.0, floor_with_chair(400, 300), {}, {20: "chair"}) == []
