"""Gyro -> yaw (pages 4, 5, 6, 9), all on the synthetic Muse."""
import numpy as np
import pytest

from headmotion import synth
from headmotion.calibration import Calibration
from headmotion.frame import wrap
from headmotion.sources import CAM, GYRO
from headmotion.yaw import HeadYawEstimator, ReferenceGlance, StillRebias, measure_bias, run_offline


def _run(moves, imu=None, cam=None, cal=None, **kw):
    truth, marks = synth.run_script(moves)
    s = synth.imu_samples(truth, imu or synth.ImuConfig())
    if cam is not None:
        s += synth.camera_samples(truth, cam)
    est = HeadYawEstimator(cal or Calibration(), **kw)
    out = run_offline(s, est, reset_yaw=None if cam is not None and kw.get("use_camera", True) else 0.0)
    t = np.array([h.t_s for h in out])
    y = np.array([np.nan if h.yaw_deg is None else h.yaw_deg for h in out])
    return truth, marks, t, y, est, out


def _err(truth, t, y):
    return np.abs(wrap(y - synth.truth_yaw_at(truth, t)))


# ---------------------------------------------------------------- bias
def test_bias_measured_and_rejected_when_moving():
    rng = np.random.default_rng(0)
    still = rng.normal([0.4, -0.2, 0.3], 0.15, (260, 3))
    assert measure_bias(still) == pytest.approx([0.4, -0.2, 0.3], abs=0.05)
    moving = still + np.outer(np.sin(np.linspace(0, 6, 260)), [0, 0, 20])
    with pytest.raises(RuntimeError, match="keep still"):
        measure_bias(moving)


# ------------------------------------------------- page 4c: tilt fix table
@pytest.mark.parametrize("pitch, roll", [(0, 0), (40, 0), (-20, 0), (0, 20)])
def test_tilt_fix_keeps_90_within_5(pitch, roll):
    truth, marks, t, y, *_ = _run(synth.tilted_turn(pitch, roll), use_camera=False)
    at = y[np.searchsorted(t, marks[0] + 0.5)]
    assert at == pytest.approx(90, abs=5)          # page 4 DONE WHEN: within ~5 deg looking down


def test_gyro_only_two_minutes_inside_budget():
    """Page 5: with bias removed, a 2-minute run of free movement stays within +-10."""
    truth, _, t, y, *_ = _run(synth.free_movement(120), use_camera=False)
    assert np.percentile(_err(truth, t, y), 95) < 10


def test_dropped_packets_are_not_turns():
    """Page 5b: skip gaps; with 3% packets dropped the angle still tracks."""
    imu = synth.ImuConfig(drop_packet_prob=0.03)
    truth, _, t, y, est, _ = _run(synth.axis_experiment(), imu=imu, use_camera=False)
    # Each dropped 3-sample packet during a turn loses ~3 samples of angle; still small
    assert np.percentile(_err(truth, t, y), 95) < 6


def test_scale_correction():
    imu = synth.ImuConfig(scale=1.033)                         # 90 reads ~93
    truth, marks, t, y, *_ = _run(synth.tilted_turn(), imu=imu, use_camera=False)
    raw = y[np.searchsorted(t, marks[0] + 0.5)]
    assert raw == pytest.approx(93, abs=1.0)
    cal = Calibration(scale=1 / 1.033)
    truth, marks, t, y, *_ = _run(synth.tilted_turn(), imu=imu, use_camera=False, cal=cal)
    assert y[np.searchsorted(t, marks[0] + 0.5)] == pytest.approx(90, abs=1.0)


# ------------------------------------------------------- auto re-bias
def test_rebias_absorbs_warmup_drift():
    """Page 5 'D1 good, D2 bad': bias wanders once warm; re-zero when still."""
    imu = synth.ImuConfig(bias_drift_dps_per_min=(0.3, 0.3, 0.3))
    moves = synth.still(5) + synth.still(240)
    *_, y_off, _, _ = _run(moves, imu=imu, use_camera=False, auto_rebias=False)
    *_, y_on, est, _ = _run(moves, imu=imu, use_camera=False, auto_rebias=True)
    assert abs(wrap(y_off[-1])) > 15          # without: drifts tens of degrees in 4 min
    assert abs(wrap(y_on[-1])) < 3            # with: stays put
    assert est.rebias_count > 0


def test_rebias_does_not_swallow_slow_turn():
    """A steady 5 deg/s turn looks 'still' (low jitter) but is far from the bias."""
    moves = synth.still(5) + [synth.Move(18.0, yaw=90)]
    truth, _, t, y, est, _ = _run(moves, use_camera=False)
    assert y[-1] == pytest.approx(90, abs=3)


def test_stillrebias_unit():
    rb = StillRebias()
    bias = np.zeros(3)
    found = [rb.update(i / 52, np.array([0.2, 0.0, 0.1]), bias) for i in range(200)]
    found = [f for f in found if f is not None]
    assert found and found[-1] == pytest.approx([0.2, 0.0, 0.1])


# ------------------------------------------------------ page 6d: resets
def test_start_of_run_reset():
    est = HeadYawEstimator(Calibration())
    est.set_bias(np.zeros(3))
    est.reset(45.0)
    assert est.yaw == 45.0
    est.reset(200.0)
    assert est.yaw == pytest.approx(-160.0)


def test_reference_glance_snaps_once():
    rg = ReferenceGlance(bearing_deg=90, tol_deg=10, hold_s=1.5)
    fired = [rg.update(i / 52, 84.0, 0.5) for i in range(52 * 3)]
    assert sum(fired) == 1                       # once per dwell
    assert not rg.update(4.0, 120.0, 0.0)        # leave the cone...
    fired = [rg.update(5 + i / 52, 95.0, 0.5) for i in range(52 * 2)]
    assert sum(fired) == 1                       # ...re-arms


def test_reference_glance_corrects_drift():
    # bias creeps 0.2 deg/s per minute -> ~8 deg of yaw drift by the end: inside the 10 deg cone
    imu = synth.ImuConfig(bias_drift_dps_per_min=(0.0, -0.2, 0.0))
    moves = synth.free_movement(65, seed=9)
    end = synth.run_script(moves)[0].yaw[-1]
    moves += [synth.Move(1.5, yaw=end + wrap(90 - end), hold=3.0)]    # short way round to the sign
    truth, _, t, y, *_ = _run(moves, imu=imu, use_camera=False, auto_rebias=False)
    assert _err(truth, t[-5:], y[-5:]).max() > 4                    # without the sign: drifted
    truth, _, t, y, *_ = _run(moves, imu=imu, use_camera=False, auto_rebias=False, reference=ReferenceGlance(90.0))
    assert _err(truth, t[-5:], y[-5:]).max() < 2                    # with it: snapped back


# --------------------------------------------- page 6b + 9: camera blend
def test_blend_repairs_clipped_fast_turn_within_1s():
    """Fast turn at ~480 deg/s clips at 245; camera pulls the error back."""
    moves = synth.still(8) + [synth.Move(0.35, yaw=90, hold=4.0, mark=True)]
    truth, marks, t, y_g, *_ = _run(moves, use_camera=False)
    gyro_err = _err(truth, t, y_g)[-1]
    assert gyro_err > 15                                   # gyro alone lost a chunk of the turn
    cam = synth.CameraConfig(lag_s=0.10)
    truth, marks, t, y, est, _ = _run(moves, cam=cam)
    assert est.clipped_samples > 0
    after = t >= marks[0] + 1.0                            # 1 s after the turn ends
    assert _err(truth, t[after], y[after]).max() < 5       # page 9 DONE WHEN


def test_blend_holds_through_10s_hidden():
    # bias creeps 0.5 deg/s per minute: harsh (D2 target is < 2 deg/min of yaw) but plausible when warming up
    imu = synth.ImuConfig(bias_drift_dps_per_min=(0.0, -0.5, 0.0))
    cam = synth.CameraConfig(hidden=((40.0, 50.0),))
    truth, _, t, y, *_ = _run(synth.free_movement(90, seed=2), imu=imu, cam=cam, auto_rebias=False)
    e = _err(truth, t, y)
    vis = (t > 20) & ((t < 40) | (t > 52))
    assert np.median(e[vis]) < 5
    assert e[np.searchsorted(t, 49.9)] < 5                 # after 10 s hidden


def test_blend_ignores_misreads_but_accepts_persistent_jump():
    cam = synth.CameraConfig(misread_prob=0.05)
    truth, _, t, y, est, _ = _run(synth.free_movement(60, seed=5), cam=cam)
    assert est.camera_rejected > 0
    assert np.median(_err(truth, t, y)[t > 10]) < 5
    # persistent: gyro thinks 0, camera consistently says 100 (worker turned while hidden + clipping)
    est = HeadYawEstimator(Calibration())
    est.set_bias(np.zeros(3)); est.on_acc(0, (0, 0, 1)); est.reset(0.0)
    used = []
    for i in range(80):
        ti = i / 52
        est.on_gyro(ti, (0, 0, 0))
        if i % 2 == 0:
            used.append(est.on_camera(ti, 100.0))
    assert not used[0] and est.yaw == pytest.approx(100.0)


def test_outputs_match_message_contract():
    *_, out = _run(synth.still(8), cam=synth.CameraConfig())
    h = out[-1]
    assert set(h.as_dict()) == {"v", "t_s", "yaw_deg", "rate_dps", "source", "cam_age_s", "valid"}
    assert h.source == "blend" and h.valid and h.cam_age_s < 0.1


# ------------------------------------------------- interrogate review fixes
def _bare_estimator(**kw):
    est = HeadYawEstimator(Calibration(), **kw)
    est.set_bias(np.zeros(3)); est.on_acc(0, (0, 0, 1)); est.reset(0.0)
    return est


def test_isolated_misreads_far_apart_are_not_accepted():
    """Review #1: two single 180-deg flips 15 s apart used to snap yaw."""
    est = _bare_estimator()
    for i in range(52 * 30):
        ti = i / 52
        est.on_gyro(ti, (0, 0, 0))
        if i % 2 == 0:
            flip = abs(ti - 10.0) < 0.02 or abs(ti - 25.0) < 0.02
            est.on_camera(ti, 178.0 if flip else 0.0)
    assert abs(est.yaw) < 1


def test_valid_is_false_while_big_error_pending():
    est = _bare_estimator()
    est.on_gyro(0.0, (0, 0, 0)); est.on_gyro(0.02, (0, 0, 0))
    est.on_camera(0.02, 120.0)
    assert not est.on_gyro(0.04, (0, 0, 0)).valid


@pytest.mark.parametrize("turn", [90, 150, 180])
def test_clipped_snap_turn_recovers_within_1s(turn):
    """Review #4: big snap turns leave >45 deg of clip error, which hits the gate."""
    moves = synth.still(8) + [synth.Move(0.3, yaw=turn, hold=4.0, mark=True)]
    truth, marks, t, y, est, _ = _run(moves, cam=synth.CameraConfig(lag_s=0.10))
    assert est.clipped_samples > 0
    after = t >= marks[0] + 1.0
    assert _err(truth, t[after], y[after]).max() < 5


def test_mirrored_gyro_is_detected_and_fixed():
    """Review #2: a gyro mirrored relative to the accel breaks 'up' propagation;
    output sign alone cannot fix it. The axis experiment finds gyro_sign."""
    from headmotion import analysis
    imu = synth.ImuConfig(gyro_sign=-1.0)
    truth, marks = synth.run_script(synth.axis_experiment())
    r = analysis.axis_experiment(synth.imu_samples(truth, imu))
    assert r["gyro_sign"] == -1.0 and r["sign"] == 1.0
    cal = Calibration(gyro_sign=r["gyro_sign"], sign=r["sign"])
    truth, _, t, y, *_ = _run(synth.free_movement(120), imu=imu, use_camera=False, cal=cal)
    assert np.percentile(_err(truth, t, y), 95) < 5


def _roll_while_pitched():
    moves = synth.still(5)
    for _ in range(2):
        moves += [synth.Move(0.8, pitch=40, hold=0.3), synth.Move(0.8, roll=20, hold=0.3),
                  synth.Move(0.8, pitch=0, hold=0.3), synth.Move(0.8, roll=0, hold=0.5)]
    return moves


def _calibrated(imu=None):
    """Calibration the way the team will get it: from an axis-experiment recording."""
    from headmotion import analysis
    truth, _ = synth.run_script(synth.axis_experiment())
    r = analysis.axis_experiment(synth.imu_samples(truth, imu or synth.ImuConfig()))
    return Calibration(gyro_sign=r["gyro_sign"], sign=r["sign"], nose_axis=r["nose_axis"])


def test_roll_while_pitched_returns_to_start():
    """Formerly a known limitation: integrating rotation about vertical is not the
    heading of the nose, so roll-while-pitched leaves ~13 deg per cycle. With the
    nose axis from the axis experiment, yaw is the nose's heading and comes back."""
    truth, _, t, y, *_ = _run(_roll_while_pitched(), use_camera=False)
    assert _err(truth, t[-1:], y[-1:])[0] > 10                       # without the nose axis
    truth, _, t, y, *_ = _run(_roll_while_pitched(), use_camera=False, cal=_calibrated())
    assert _err(truth, t[-1:], y[-1:])[0] < 2
    assert np.percentile(_err(truth, t, y), 95) < 5                  # and on the way, not just at the end


@pytest.mark.parametrize("imu", [synth.ImuConfig(), synth.ImuConfig(gyro_sign=-1.0),
                                 synth.ImuConfig(mount=np.eye(3))], ids=["default", "mirrored", "identity"])
def test_nose_mode_keeps_guide_targets(imu):
    """Page 4 + 5 targets still hold in nose mode, whatever the mount."""
    cal = _calibrated(imu)
    for pitch, roll in [(40, 0), (0, 20)]:
        truth, marks, t, y, *_ = _run(synth.tilted_turn(pitch, roll), imu=imu, use_camera=False, cal=cal)
        assert y[np.searchsorted(t, marks[0] + 0.5)] == pytest.approx(90, abs=5)
    truth, _, t, y, *_ = _run(synth.free_movement(120), imu=imu, use_camera=False, cal=cal)
    assert np.percentile(_err(truth, t, y), 95) < 5


def test_nose_mode_tolerates_a_rough_nose_axis():
    off = np.radians(10)                                             # true nose is -gz, ear is gx
    cal = Calibration(nose_axis=[np.sin(off), 0.0, -np.cos(off)])
    truth, _, t, y, *_ = _run(synth.free_movement(120), use_camera=False, cal=cal)
    assert np.percentile(_err(truth, t, y), 95) < 5


def test_nose_mode_with_camera_blend():
    cal = _calibrated()
    cam = synth.CameraConfig(hidden=((40.0, 50.0),), misread_prob=0.02)
    truth, _, t, y, *_ = _run(synth.free_movement(90, seed=2), cam=cam, cal=cal)
    e = _err(truth, t, y)
    assert np.median(e[t > 20]) < 3 and e[np.searchsorted(t, 49.9)] < 5


def test_blend_learns_bias_so_late_blind_spots_stay_small():
    """Page 9 DONE WHEN: under 5 deg after 10 s hidden. With the bias creeping as the
    band warms (0.3 deg/s per minute), a pull on yaw alone leaves the bias wrong, so
    a blind spot late in the session drifted ~9 deg. Camera errors now also re-learn
    the bias about vertical (blend_ki)."""
    imu = synth.ImuConfig(bias_drift_dps_per_min=(0.0, -0.3, 0.0))
    cam = synth.CameraConfig(hidden=((160.0, 170.0),))
    moves = synth.free_movement(180, seed=0)
    no_ki = _run(moves, imu=imu, cam=cam, cal=Calibration(blend_ki=0.0))
    with_ki = _run(moves, imu=imu, cam=cam)
    at = lambda r: _err(r[0], r[2], r[3])[np.searchsorted(r[2], 169.9)]
    assert at(no_ki) > 5
    assert at(with_ki) < 5


def test_bias_learning_skips_clip_recovery():
    est = _bare_estimator()
    est.on_gyro(0.0, (0, 0, 0)); est.on_gyro(0.02, (0, 0, 300.0))   # clipped sample
    before = est.bias.copy()
    est.on_gyro(0.04, (0, 0, 0))
    est.on_camera(0.04 + est.cal.camera_lag_s, 10.0)
    assert np.array_equal(est.bias, before)                          # a clip error is not a bias
