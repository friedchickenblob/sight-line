import pytest

from vision.hazards import bearing_deg, in_view, relative_bearing_deg, wrap180

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

