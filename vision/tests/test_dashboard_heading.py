"""DashboardHeading: the vision heading taken from the dashboard's /api/state."""

import urllib.error

import pytest

from vision.dashboard_heading import DashboardHeading


def state(yaw=12.0, t_s=1.0, valid=True, connected=True, bias=0.1):
    return {"yaw": None if yaw is None else {"yaw_deg": yaw, "t_s": t_s, "valid": valid},
            "health": {"connected": connected}, "bias": bias}


def heading(responses, **kw):
    it = iter(responses)

    def fetch():
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    return DashboardHeading(fetch=fetch, **kw)


def test_takes_the_dashboards_yaw():
    h = heading([state(yaw=37.5)])
    h.poll_once()
    assert h.yaw_deg == pytest.approx(37.5)
    assert h.state == "running" and "dashboard muse" in h.status


def test_flip_negates():
    h = heading([state(yaw=20.0)], flip=True)
    h.poll_once()
    assert h.yaw_deg == pytest.approx(-20.0)


def test_not_ready_until_the_dashboard_has_a_yaw():
    h = heading([state(yaw=None, bias=None)])
    h.poll_once()
    assert h.yaw_deg is None and h.state == "hold still"


def test_dashboard_not_running():
    h = heading([urllib.error.URLError("refused")])
    h.poll_once()
    assert h.yaw_deg is None and h.state == "no dashboard"


def test_stale_when_the_dashboard_stops_updating(monkeypatch):
    h = heading([state(yaw=5.0, t_s=1.0), state(yaw=5.0, t_s=1.0)])
    h.poll_once()
    h._t_update -= DashboardHeading.STALE_S + 1          # the same estimate, seen again 3 s later
    h.poll_once()
    assert h.yaw_deg is None and h.status == "dashboard muse: NO DATA"


def test_band_disconnected():
    h = heading([state(yaw=5.0, connected=False)])
    h.poll_once()
    assert h.yaw_deg is None
