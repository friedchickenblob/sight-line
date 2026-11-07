"""Frame convention + contact maths (page 2, page 8)."""
import numpy as np
import pytest

from headmotion.contact import ApproachFlag, contact, estimate_velocity
from headmotion.contact_sim import all_scenes, pass_by, run_scene, stop_short, straight_at, transitions, worker_walks
from headmotion.frame import angle_diff, wrap


@pytest.mark.parametrize("a, want", [(0, 0), (180, -180), (-180, -180), (179.9, 179.9), (540, -180), (-190, 170), (360, 0)])
def test_wrap(a, want):
    assert wrap(a) == pytest.approx(want)


def test_wrap_array_and_diff():
    assert np.allclose(wrap(np.array([190.0, -190.0])), [-170.0, 170.0])
    assert angle_diff(10, 350) == pytest.approx(20)
    assert angle_diff(350, 10) == pytest.approx(-20)


# ------------------------------------------------------------- contact()
def test_head_on_ttc_is_exact():
    g = contact(pw=(0, 0), pm=(3, 0), vm=(-1, 0), R=0.5, T=3.0)
    assert g.d == pytest.approx(3.0)
    assert g.ttc == pytest.approx(2.5)             # gap 3 -> 0.5 at 1 m/s
    assert g.d_min == pytest.approx(0.0, abs=1e-9)
    assert g.t_cpa == pytest.approx(3.0)
    assert g.approaching


def test_pass_by_never_reaches_R():
    g = contact(pw=(0, 0), pm=(2, 1.0), vm=(-1, 0), R=0.5)
    assert g.ttc is None and not g.approaching
    assert g.d_min == pytest.approx(1.0)


def test_moving_away_is_not_approaching():
    g = contact(pw=(0, 0), pm=(1, 0), vm=(1, 0), R=0.5)
    assert g.ttc is None and g.t_cpa < 0
    assert g.d_min == pytest.approx(1.0)           # closest is now


def test_stationary_inside_and_outside_R():
    assert contact((0, 0), (0.3, 0), (0, 0)).approaching
    assert not contact((0, 0), (1.0, 0), (0, 0)).approaching


def test_worker_velocity_counts():
    # Parked machine, worker walking straight at it: same as machine driving at worker.
    a = contact(pw=(0, 0), pm=(2, 0), vm=(0, 0), vw=(1, 0))
    b = contact(pw=(0, 0), pm=(2, 0), vm=(-1, 0), vw=(0, 0))
    assert a.ttc == pytest.approx(b.ttc)


@pytest.mark.parametrize("pm, want", [((1, 0), 0), ((0, 1), 90), ((-1, 0), -180), ((0, -1), -90)])
def test_bearing_uses_floor_frame(pm, want):
    assert contact((0, 0), pm, (0, 0)).bearing == pytest.approx(want)   # already wrapped, no help needed


def test_bearing_straight_behind_is_minus_180():
    """arctan2 returns +180 here; the frame convention is [-180, 180)."""
    assert contact((0, 0), (-1, 0.0), (0, 0)).bearing == -180.0
    assert contact((0, 0), (-1, -0.0), (0, 0)).bearing == -180.0


def test_hysteresis_needs_clear_for_hold_time():
    f = ApproachFlag(T=2.0, off_margin_s=0.5, hold_s=0.3)
    assert f.update(0.0, 1.9)
    assert f.update(0.1, 2.2)          # between T and T+0.5: stays on
    assert f.update(0.2, 2.6)          # clear starts
    assert f.update(0.4, 2.6)          # only 0.2 s clear
    assert not f.update(0.55, 2.6)     # 0.35 s clear -> off
    assert f.update(0.6, 1.0)          # back on immediately


def test_velocity_estimate_noise():
    rng = np.random.default_rng(0)
    t = np.arange(0, 1, 1 / 30)
    p = np.outer(t, [0.8, 0]) + rng.uniform(-0.02, 0.02, (len(t), 2))
    v = estimate_velocity(t, p, 0.5)
    assert v == pytest.approx([0.8, 0.0], abs=0.06)


# --------------------------------------------- the five synthetic scenes
@pytest.mark.parametrize("seed", range(5))
def test_scene_straight_at(seed):
    sc = straight_at()
    r = run_scene(sc, seed=seed)
    t_contact = (3.0 - 0.5) / 0.8
    first_on = r["t"][r["flag"]][0]
    assert t_contact - first_on == pytest.approx(2.0, abs=0.25)   # ~T seconds before contact
    assert transitions(r["flag"]) == 1                              # on once, stays on


@pytest.mark.parametrize("seed", range(5))
def test_scene_pass_clear_never_fires(seed):
    assert not run_scene(pass_by(1.2), seed=seed)["flag"].any()


@pytest.mark.parametrize("seed", range(5))
def test_scene_near_miss_fires_without_flicker(seed):
    r = run_scene(pass_by(0.3), seed=seed)
    assert r["flag"].any()
    assert transitions(r["flag"]) <= 2


@pytest.mark.parametrize("seed", range(5))
def test_scene_stop_short_turns_off(seed):
    r = run_scene(stop_short(), seed=seed)
    assert r["flag"].any() and not r["flag"][-1]
    assert transitions(r["flag"]) == 2


@pytest.mark.parametrize("seed", range(5))
def test_scene_worker_walks(seed):
    assert run_scene(worker_walks(0.3), seed=seed)["flag"].any()
    assert not run_scene(worker_walks(1.0), seed=seed)["flag"].any()


def test_hysteresis_reduces_flicker_across_scenes():
    raw = sum(transitions(run_scene(s, seed=k, hysteresis=False)["flag"]) for s in all_scenes() for k in range(5))
    hyst = sum(transitions(run_scene(s, seed=k)["flag"]) for s in all_scenes() for k in range(5))
    assert hyst < raw
