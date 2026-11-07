import pytest

from vision.hazards import assess, bearing_deg, in_view, relative_bearing_deg, wrap180

W = (1.0, 1.0)  # worker position used in the doc's examples


@pytest.mark.parametrize("car, expected", [
    ((2.0, 1.0), 0.0),      # toward +x
    ((1.0, 2.0), 90.0),     # toward +y
    ((0.0, 1.0), -180.0),   # toward -x (wrapped to [-180, 180))
    ((1.0, 0.0), -90.0),    # toward -y
    ((2.0, 2.0), 45.0),
])
def test_bearing_matches_doc_table(car, expected):
    assert bearing_deg(W, car) == pytest.approx(expected)


@pytest.mark.parametrize("bearing, rel", [(90, 0), (-180, 90), (0, -90), (-90, -180)])
def test_relative_bearing_worker_facing_plus_y(bearing, rel):
    assert relative_bearing_deg(bearing, head_yaw=90) == pytest.approx(rel)


def test_wrap_and_view():
    assert wrap180(180) == -180 and wrap180(190) == -170 and wrap180(-190) == 170
    assert in_view(50) and not in_view(70) and not in_view(-180)


def test_head_on_approach_is_threat():
    g = assess(W, (0, 0), (4.0, 1.0), (-1.0, 0.0))      # 3 m away, 1 m/s straight at worker
    assert g.distance_m == pytest.approx(3.0) and g.bearing_deg == pytest.approx(0.0)
    assert g.closing_speed_mps == pytest.approx(1.0)
    assert g.t_cpa_s == pytest.approx(3.0) and g.cpa_m == pytest.approx(0.0)
    assert not g.threat                                  # contact at exactly the 3 s horizon edge
    assert assess(W, (0, 0), (3.5, 1.0), (-1.0, 0.0)).threat


def test_passing_wide_is_not_threat():
    g = assess(W, (0, 0), (3.0, 3.0), (-1.0, 0.0))      # passes 2 m away
    assert g.cpa_m == pytest.approx(2.0) and not g.threat


def test_receding_and_stationary():
    rec = assess(W, (0, 0), (2.0, 1.0), (1.0, 0.0))
    assert rec.closing_speed_mps < 0 and rec.t_cpa_s == 0 and not rec.threat
    still = assess(W, (0, 0), (3.0, 1.0), (0.0, 0.0))
    assert still.t_cpa_s == 0 and still.cpa_m == pytest.approx(2.0) and not still.threat


def test_inside_radius_is_threat_even_if_still():
    assert assess(W, (0, 0), (1.5, 1.0), (0.0, 0.0)).threat


def test_worker_walking_into_parked_car():
    g = assess(W, (1.0, 0.0), (3.0, 1.0), (0.0, 0.0))   # relative motion is what counts
    assert g.closing_speed_mps == pytest.approx(1.0) and g.threat
