"""Replay safety net, health watchdog, and the experiment analysis code."""
import numpy as np
import pytest

from headmotion import analysis, synth
from headmotion.calibration import Calibration
from headmotion.health import StreamHealth, gap_stats
from headmotion.sources import CsvRecorder, DROP, GYRO, Replay, Sample, read_columns_csv, read_csv, write_csv
from headmotion.yaw import HeadYawEstimator, run_offline


def _samples(moves, imu=None, cam=None):
    truth, marks = synth.run_script(moves)
    s = synth.imu_samples(truth, imu or synth.ImuConfig()) + synth.mark_samples(marks)
    if cam:
        s += synth.camera_samples(truth, cam)
    return truth, sorted(s, key=lambda x: x.t)


# --------------------------------------------------------------- replay
def test_csv_round_trip_and_replay_is_identical(tmp_path):
    """Page 7 DONE WHEN: a recorded session replays through the yaw code."""
    _, s = _samples(synth.axis_experiment(), cam=synth.CameraConfig())
    p = tmp_path / "session.csv"
    write_csv(p, s)
    back = read_csv(p)
    assert len(back) == len(s) and back[100] == s[100]
    direct = run_offline(s, HeadYawEstimator())
    replayed = []
    with CsvRecorder(tmp_path / "again.csv") as rec:
        for batch in rec.tee(Replay(back).batches()):
            replayed += batch
    assert rec.count == len(s)
    again = run_offline(replayed, HeadYawEstimator())
    assert [h.yaw_deg for h in direct] == [h.yaw_deg for h in again]


def test_replay_batches_are_time_ordered_and_can_be_empty():
    s = [Sample(GYRO, 0.0, (0, 0, 0)), Sample(GYRO, 1.0, (0, 0, 0))]
    batches = list(Replay(s, batch_s=0.1).batches())
    assert sum(len(b) for b in batches) == 2
    assert any(len(b) == 0 for b in batches)       # the gap shows up as empty batches


def test_read_columns_csv(tmp_path):
    p = tmp_path / "cam.csv"
    p.write_text("t,yaw_deg\n1.0,10\n1.033,\n1.066,12\n")
    s = read_columns_csv(p, "CAM")
    assert [x.v for x in s] == [(10.0,), (12.0,)]


# --------------------------------------------------------------- health
def test_watchdog_and_drops():
    h = StreamHealth()
    for i in range(52):
        h.on_sample(i / 52)
    assert not h.stale(1.0)
    assert h.stale(1.6)
    h.on_sample(1.3)                               # 0.32 s gap, no DROP sample (old recording) = one drop
    r = h.report(1.31)
    assert r.connected and h.total_drops == 1
    assert r.drops_per_min == pytest.approx(60 / 1.31)   # rate over the 1.31 s watched, not a full minute


def test_drop_sample_is_not_counted_twice():
    h = StreamHealth()
    h.on_sample(0.0)
    h.on_drop(0.5, 24)                             # the packet counter explains the 0.5 s gap...
    h.on_sample(0.5)
    assert h.total_drops == 1 and h.lost_samples == 24   # ...so the timing gap is not a second drop
    h.on_sample(0.52)
    h.on_drop(0.54, 3)                             # a single lost packet: under 100 ms, still counted
    h.on_sample(0.54)
    assert h.total_drops == 2


def test_drops_per_min_settles_to_a_full_window():
    h = StreamHealth()
    for i in range(52 * 120):
        h.on_sample(i / 52)
    h.on_drop(100.0, 3)
    assert h.report(119.9).drops_per_min == pytest.approx(1.0)


def test_gap_stats_on_synthetic_drops():
    _, s = _samples(synth.still(60), imu=synth.ImuConfig(drop_packet_prob=0.02))
    st = analysis.bluetooth(s)
    n_drop = sum(1 for x in s if x.kind == DROP)
    assert st["rate_hz"] == pytest.approx(52, abs=1)
    assert n_drop > 0 and st["drops"] == n_drop    # every lost packet seen, though each is only 58 ms
    assert st["lost_samples"] == sum(x.v[0] for x in s if x.kind == DROP) and st["lost_samples"] % 3 == 0
    _, s = _samples(synth.still(60), imu=synth.ImuConfig(drop_packet_prob=0.0))
    t = np.array([x.t for x in s if x.kind == GYRO])
    t = np.r_[t[:1000], t[1010:] + 0.0]            # cut 10 samples = ~190 ms gap
    assert gap_stats(t)["drops"] == 1


# ------------------------------------------------------------- analysis
def test_axis_experiment_finds_hidden_axis_and_sign():
    _, s = _samples(synth.axis_experiment())
    r = analysis.axis_experiment(s)
    assert r["axis_name"] == "gy" and r["axis_sign"] == -1.0    # the synthetic mount hides yaw on -gy
    assert r["sign"] == 1.0
    assert r["first_left_turn_deg"] == pytest.approx(90, abs=5)


def test_axis_experiment_with_flipped_mount():
    imu = synth.ImuConfig(mount=np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]))
    _, s = _samples(synth.axis_experiment(), imu=imu)
    r = analysis.axis_experiment(s)
    assert r["axis_name"] == "gz" and r["axis_sign"] == 1.0


def test_tilt_comparison_table_row():
    cal = Calibration(yaw_axis=1, axis_sign=-1.0)
    _, s = _samples(synth.tilted_turn(pitch=40), imu=synth.ImuConfig())
    r = analysis.tilt_comparison(s, cal)
    assert r["axis_only_deg"] == pytest.approx(90 * np.cos(np.radians(40)), abs=3)
    assert r["tilt_fix_deg"] == pytest.approx(90, abs=3)


def test_drift_suggests_rebias_threshold_from_still_noise():
    r = analysis.drift_still(_samples(synth.still(30), imu=synth.ImuConfig(noise_dps=0.4))[1])
    assert r["still_noise_dps"] == pytest.approx(0.4, abs=0.05)
    assert r["suggested_rebias_max_std_dps"] == pytest.approx(0.8, abs=0.1)


def test_rebias_threshold_comes_from_calibration():
    imu = synth.ImuConfig(noise_dps=0.8, bias_drift_dps_per_min=(0.3, 0.3, 0.3))
    _, s = _samples(synth.still(5) + synth.still(60), imu=imu)
    strict = HeadYawEstimator(Calibration(rebias_max_std_dps=0.6), use_camera=False)
    run_offline(s, strict)
    assert strict.rebias_count == 0                  # 0.8 deg/s noise: the 0.6 default never sees "still"
    suggested = analysis.drift_still(s)["suggested_rebias_max_std_dps"]
    tuned = HeadYawEstimator(Calibration(rebias_max_std_dps=suggested), use_camera=False)
    run_offline(s, tuned)
    assert tuned.rebias_count > 0


def test_axis_experiment_finds_nose_axis():
    _, s = _samples(synth.axis_experiment())
    r = analysis.axis_experiment(s)
    assert abs(r["nose_axis"][2]) == pytest.approx(1.0, abs=0.01)   # synthetic mount: nose on gz
    assert r["nose_check_deg"] < 5                                  # nods and tilts agree
    imu = synth.ImuConfig(mount=np.eye(3))
    r = analysis.axis_experiment(_samples(synth.axis_experiment(), imu=imu)[1])
    assert abs(r["nose_axis"][0]) == pytest.approx(1.0, abs=0.01)


def test_drift_still_measures_slope():
    imu = synth.ImuConfig(bias_drift_dps_per_min=(0.0, -0.05, 0.0))   # slowly growing bias
    _, s = _samples(synth.still(5) + synth.still(120), imu=imu)
    off = analysis.drift_still(s, auto_rebias=False)
    assert off["drift_deg_per_min"] > 0                              # -gy bias -> +yaw drift
    on = analysis.drift_still(s, auto_rebias=True)
    assert abs(on["total_deg"]) < abs(off["total_deg"])


def test_drift_at_marks():
    moves = synth.still(5)
    for _ in range(4):
        moves += synth.free_movement(25, seed=7)[1:] + [synth.Move(1.0, yaw=0, hold=2, mark=True)]
    _, s = _samples(moves)
    r = analysis.drift_at_marks(s, 0.0)
    assert len(r["error_deg"]) == 4 and abs(r["final_error_deg"]) < 10


def test_scale_factor_recovers_scale_error():
    _, s = _samples(synth.tape_lines(), imu=synth.ImuConfig(scale=1.033))
    r = analysis.scale_factor(s)
    assert r["scale"] == pytest.approx(1 / 1.033, abs=0.006)
    assert r["measured_deg"][1] == pytest.approx(93, abs=1.5)


def test_fast_turns_detect_clipping():
    _, s = _samples(synth.fast_turns(n=3, peak_s=0.3))
    r = analysis.fast_turns(s)
    assert r["flat_topped_peaks"] >= 3 and r["peak_dps"] == pytest.approx(245, abs=1)
    assert abs(r["error_after_each_deg"][0]) > 10                    # lost part of the first turn
    _, s = _samples(synth.fast_turns(n=3, peak_s=1.0))               # slow enough not to clip
    assert analysis.fast_turns(s)["flat_topped_peaks"] == 0


def test_glance_durations():
    truth, s = _samples(synth.glances(target=60, n=10, dwell_s=(0.1, 0.2)))
    t, y = analysis.gyro_yaw(s)
    d = analysis.glance_durations(t, y, 60)
    d_true = analysis.glance_durations(t, synth.truth_yaw_at(truth, t), 60)
    assert len(d) == len(d_true) == 10
    assert d == pytest.approx(d_true, abs=0.05)    # time inside the +-30 cone, including turning through it


def test_estimate_lag():
    _, s = _samples(synth.free_movement(60, seed=1), cam=synth.CameraConfig(lag_s=0.12))
    r = analysis.estimate_lag(s)
    assert r["lag_s"] == pytest.approx(0.12, abs=0.02) and r["peak_corr"] > 0.6


def test_blend_sweep_table():
    truth, s = _samples(synth.free_movement(90, seed=2), imu=synth.ImuConfig(bias_drift_dps_per_min=(0, -0.3, 0)),
                        cam=synth.CameraConfig())
    rows = analysis.blend_sweep(s, ks=(0.02, 0.05, 0.2), truth=lambda t: synth.truth_yaw_at(truth, t))
    assert [r["K"] for r in rows] == [0.02, 0.05, 0.2]
    k05 = rows[1]
    assert k05["err_visible_deg"] < 5 and k05["drift_after_hidden_deg"] < 5


def test_calibration_pending_and_round_trip(tmp_path):
    c = Calibration()
    assert "sign" in c.pending() and "scale" in c.pending()
    c.scale = 0.97
    c.status["scale"] = "measured 2026-11-02, 3 people, tape lines"
    c.save(tmp_path / "cal.json")
    c2 = Calibration.load(tmp_path / "cal.json")
    assert c2.scale == 0.97 and "scale" not in c2.pending()


def test_reset_is_recorded_and_replays_identically(tmp_path):
    """Review #6: the live loop's re-zero must be in the recording, and replay must
    go through the same YawPipeline the live script uses."""
    from headmotion.sources import RESET
    from headmotion.yaw import YawPipeline
    _, s = _samples(synth.free_movement(30, seed=3))
    s.append(Sample(RESET, 20.0, (0.0,)))
    s.sort(key=lambda x: x.t)
    live = []
    with CsvRecorder(tmp_path / "live.csv") as rec:
        pipe = YawPipeline(HeadYawEstimator(), retry_bias=True)
        for batch in rec.tee(Replay(s).batches()):
            live += [h for h in map(pipe.feed, batch) if h is not None]
    assert "re-zeroed" in pipe.events
    replayed = run_offline(read_csv(tmp_path / "live.csv"), HeadYawEstimator())
    assert [h.yaw_deg for h in live] == [h.yaw_deg for h in replayed]
    i = next(k for k, h in enumerate(live) if h.t_s >= 20.0)
    assert abs(live[i].yaw_deg) < 3.0               # re-zeroed at t = 20 s (mid-turn: one 19 ms step of rotation)


def test_calibration_new_fields_stay_pending_in_old_files(tmp_path):
    import json
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"scale": 0.97, "status": {"scale": "measured"}}))
    c = Calibration.load(p)
    assert c.scale == 0.97 and "scale" not in c.pending() and "gyro_sign" in c.pending()
