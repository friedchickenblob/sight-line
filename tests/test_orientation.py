import math

import numpy as np

from sightline.orientation import (HeadOrientationTracker, calibrate_still, quat_from_two_vectors,
                                   quat_rotate)

FS = 52.0
BIAS = np.array([0.8, -0.5, 0.3])  # deg/s


def _still(up_sensor, n=260, rng=None):
    rng = rng or np.random.default_rng(0)
    a = np.tile(up_sensor, (n, 1)) + rng.normal(0, 0.005, (n, 3))
    g = np.tile(BIAS, (n, 1)) + rng.normal(0, 0.3, (n, 3))
    return a, g


def _turn(tracker, up_sensor, rate_dps, seconds, rng):
    """Rotate about the true vertical; in the sensor frame that's the 'up' axis."""
    s = None
    for _ in range(int(seconds * FS)):
        g = up_sensor * (-rate_dps) + BIAS + rng.normal(0, 0.3, 3)  # right turn = clockwise from above
        a = up_sensor + rng.normal(0, 0.005, 3)
        s = tracker.update(a, g)
    return s


def test_heading_upright_mount():
    rng = np.random.default_rng(1)
    up = np.array([0, 0, 1.0])
    cal = calibrate_still(*_still(up, rng=rng))
    assert cal.is_still
    tr = HeadOrientationTracker(cal, FS, "+x")
    s = _turn(tr, up, 30, 3, rng)          # 90 deg right -> -90 (CCW positive)
    assert abs(s.heading_deg + 90) < 2, s
    assert abs(s.pitch_deg) < 2 and abs(s.roll_deg) < 2
    s = _turn(tr, up, -45, 4, rng)         # 180 deg left -> +90
    assert abs(s.heading_deg - 90) < 3, s


def test_heading_tilted_mount_is_mount_independent():
    rng = np.random.default_rng(2)
    up = np.array([0.3, -0.2, 0.93]); up /= np.linalg.norm(up)  # headband sits tilted
    cal = calibrate_still(*_still(up, rng=rng))
    tr = HeadOrientationTracker(cal, FS, "+x")
    s = _turn(tr, up, 20, 3, rng)          # 60 deg right -> -60
    assert abs(s.heading_deg + 60) < 2, s


def test_tare_and_drift_when_still():
    rng = np.random.default_rng(3)
    up = np.array([0, 0, 1.0])
    cal = calibrate_still(*_still(up, rng=rng))
    tr = HeadOrientationTracker(cal, FS, "+x")
    _turn(tr, up, 30, 2, rng)
    tr.tare()
    s = _turn(tr, up, 0, 60, rng)          # sit still for a minute
    assert abs(s.heading_deg) < 1.5, s     # bias was removed by calibration


def test_uncalibrated_bias_drifts_but_auto_bias_fixes_it():
    rng = np.random.default_rng(4)
    up = np.array([0, 0, 1.0])
    a, g = _still(up, rng=rng)
    cal = calibrate_still(a, g - BIAS)     # pretend calibration missed the bias
    plain = HeadOrientationTracker(cal, FS, "+x")
    auto = HeadOrientationTracker(cal, FS, "+x", auto_bias=True, auto_bias_tau_s=5)
    for _ in range(int(120 * FS)):
        gg = BIAS + rng.normal(0, 0.3, 3)
        aa = up + rng.normal(0, 0.005, 3)
        sp, sa = plain.update(aa, gg), auto.update(aa, gg)
    assert abs(sp.heading_deg) > 10        # ~0.3 deg/s * 120 s of drift
    assert abs(sa.heading_deg) < abs(sp.heading_deg) / 4


def test_pitch_sign():
    rng = np.random.default_rng(5)
    up = np.array([0, 0, 1.0])
    cal = calibrate_still(*_still(up, rng=rng))
    tr = HeadOrientationTracker(cal, FS, "+x")
    # look up 30 deg: forward (+x) tilts toward +z. Rotation about -y (right axis is -y).
    q = quat_from_two_vectors(np.array([1.0, 0, 0]), np.array([math.cos(math.radians(30)), 0,
                                                               math.sin(math.radians(30))]))
    # gravity in the rotated body frame = R^T * up
    qc = q * np.array([1, -1, -1, -1])
    a_body = quat_rotate(qc, up)
    s = None
    for _ in range(int(10 * FS)):         # let accel correction pull pitch over
        s = tr.update(a_body, BIAS)
    assert abs(s.pitch_deg - 30) < 2, s
