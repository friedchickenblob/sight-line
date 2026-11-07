"""Analysis for every experiment in the guide. Each function takes a list of
Samples (a real recording or a synthetic one) and returns plain numbers, so
the same code fills in the guide's tables once hardware is here.

Recording protocol assumed by all of them: the first 5 s are standing still
(bias), and MARK samples are logged by pressing Enter during record.py.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from .calibration import Calibration
from .frame import unwrap, wrap
from .health import gap_stats
from .sources import ACC, CAM, DROP, GYRO, MARK
from .yaw import HeadYawEstimator, Orientation, integrate, measure_bias, run_offline

BIAS_S = 5.0


# ------------------------------------------------------------------ helpers
def arrays(samples, kind):
    rows = [(s.t, *s.v) for s in samples if s.kind == kind]
    a = np.array(rows, float) if rows else np.zeros((0, 4))
    return a[:, 0], a[:, 1:]


def mark_times(samples) -> np.ndarray:
    return np.array([s.t for s in samples if s.kind == MARK], float)


def _up_trace(samples, bias, gyro_sign: float, alpha: float):
    """'Up' in sensor axes at each gyro sample, tracked the same way as the estimator."""
    tg, g = arrays(samples, GYRO)
    ta, a = arrays(samples, ACC)
    grav = Orientation(alpha)
    up, acc_at = np.empty_like(g), np.empty_like(g)
    j = 0
    for i, t in enumerate(tg):                       # accel and gyro arrive interleaved
        while j < len(ta) and ta[j] <= t:
            grav.update(a[j]); j += 1
        if i:
            grav.propagate(gyro_sign * (g[i] - bias), t - tg[i - 1])
        up[i] = grav.up if grav.up is not None else (0, 0, 1)
        acc_at[i] = a[max(j - 1, 0)]
    return tg, g, up, acc_at


def gyro_handedness(samples, bias_s: float = BIAS_S) -> dict:
    """Is the gyro mirrored relative to the accelerometer? Rotate 'up' with the gyro
    alone (no accel correction) both ways; the right sign keeps agreeing with the
    accelerometer through nods and tilts, the wrong one wanders off."""
    tg, g = arrays(samples, GYRO)
    bias = measure_bias(g[tg < tg[0] + bias_s])
    resid = {}
    for sgn in (1.0, -1.0):
        _, _, up, acc = _up_trace(samples, bias, sgn, alpha=0.0)
        acc = acc / np.linalg.norm(acc, axis=1, keepdims=True)
        resid[sgn] = float(np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", up, acc), -1, 1))).mean())
    best = min(resid, key=resid.get)
    return dict(gyro_sign=best, mean_angle_deg={"+1": resid[1.0], "-1": resid[-1.0]})


def tilt_fixed_rate(samples, bias_s: float = BIAS_S, alpha: float = 0.02, gyro_sign: float = 1.0):
    """Gyro-only rate about true vertical, output sign NOT applied. Returns t, rate, bias."""
    tg, g = arrays(samples, GYRO)
    bias = measure_bias(g[tg < tg[0] + bias_s])
    tg, g, up, _ = _up_trace(samples, bias, gyro_sign, alpha)
    return tg, gyro_sign * np.einsum("ij,ij->i", g - bias, up), bias


def gyro_yaw(samples, cal: Optional[Calibration] = None, **kw):
    """Gyro-only yaw trace (t, yaw_deg wrapped) through the real estimator."""
    out = run_offline(samples, HeadYawEstimator(cal or Calibration(), use_camera=False, **kw))
    return np.array([h.t_s for h in out]), np.array([h.yaw_deg for h in out], float)


def median_filter(x, n: int = 5) -> np.ndarray:
    """Running median over n samples: removes single-frame camera misreads."""
    x = np.asarray(x, float)
    pad = np.pad(x, n // 2, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(pad, n), axis=1)


def _segments(active: np.ndarray, min_len: int = 5):
    """[start, end) index pairs of runs where `active` is True."""
    edges = np.flatnonzero(np.diff(np.r_[0, active.astype(int), 0]))
    return [(a, b) for a, b in zip(edges[::2], edges[1::2]) if b - a >= min_len]


# --------------------------------------------------------- page 4a: axis + sign
def _principal_axis(w) -> np.ndarray:
    """Main rotation axis of a stretch of gyro rows (sign-free: eigenvector of sum w w^T)."""
    return np.linalg.eigh(w.T @ w)[1][:, -1]


def nose_axis(samples, bias_s: float = BIAS_S, gyro_sign: float = 1.0, move_dps: float = 20.0) -> dict:
    """The head's forward axis in gyro axes, from the nods and ear-to-shoulder tilts (page 4a).

    A nod turns about the ear-to-ear axis, a tilt about the nose. Both are
    horizontal while standing level, so: up from the still window, ear from the
    nods (the first non-turn movements in the protocol), nose = ear x up, checked
    against the tilts. The sign does not matter: the start-of-run reset absorbs
    a 180 deg offset."""
    tg, g = arrays(samples, GYRO)
    ta, a = arrays(samples, ACC)
    still = tg < tg[0] + bias_s
    bias = measure_bias(g[still])
    up = a[ta < tg[0] + bias_s].mean(axis=0)
    up /= np.linalg.norm(up)
    w = gyro_sign * (g - bias)
    w, tg = w[~still], tg[~still]
    mag = np.linalg.norm(w, axis=1)
    vertical = np.abs(w @ up)
    segs = _segments((mag > move_dps) & (vertical < 0.35 * mag))
    if not segs:
        raise RuntimeError("no nods or tilts found: the axis recording needs 3 nods then 3 ear-to-shoulder tilts")
    flat = lambda v: (lambda h: h / np.linalg.norm(h))(v - (v @ up) * up)
    axes = [flat(_principal_axis(w[a:b])) for a, b in segs]
    first = axes[0]
    nods = [k for k, x in enumerate(axes) if abs(x @ first) > np.cos(np.radians(45))]
    tilts = [k for k in range(len(axes)) if k not in nods]
    ear = flat(_principal_axis(np.vstack([w[segs[k][0]:segs[k][1]] for k in nods])))
    nose = np.cross(ear, up)
    nose /= np.linalg.norm(nose)
    check = None
    if tilts:
        n2 = flat(_principal_axis(np.vstack([w[segs[k][0]:segs[k][1]] for k in tilts])))
        n2 = n2 if n2 @ nose > 0 else -n2
        check = float(np.degrees(np.arccos(np.clip(n2 @ nose, -1, 1))))
        nose = (nose + n2) / np.linalg.norm(nose + n2)
    return dict(nose_axis=np.round(nose, 4).tolist(), nods=len(nods), tilts=len(tilts), nose_check_deg=check)


def axis_experiment(samples, bias_s: float = BIAS_S, move_dps: float = 20.0) -> dict:
    """Which raw axis is yaw, with which sign, and does the first left turn sum to ~90?

    Protocol (page 4a): the FIRST movement after the still window is a left turn.
    The yaw axis is the raw axis best correlated with the tilt-fixed rate.
    Needs the nods and tilts too: they are what reveal a mirrored gyro."""
    hand = gyro_handedness(samples, bias_s)
    t, r, bias = tilt_fixed_rate(samples, bias_s, gyro_sign=hand["gyro_sign"])
    _, g = arrays(samples, GYRO)
    keep = t >= t[0] + bias_s
    t, r, g = t[keep], r[keep], g[keep] - bias
    corr = np.array([np.corrcoef(g[:, k], r)[0, 1] for k in range(3)])
    axis = int(np.argmax(np.abs(corr)))
    segs = _segments(np.abs(r) > move_dps)
    if not segs:
        raise RuntimeError("no movement found after the still window")
    a, b = segs[0]
    a, b = max(a - 10, 0), min(b + 10, len(t))      # include the slow start/end of the turn
    first_turn = float(integrate(t[a:b], r[a:b])[-1])
    sign = 1.0 if first_turn > 0 else -1.0
    axis_sign = sign * float(np.sign(corr[axis]))
    nose = nose_axis(samples, bias_s, hand["gyro_sign"])
    return dict(yaw_axis=axis, axis_name="gx gy gz".split()[axis], axis_sign=axis_sign, sign=sign,
                gyro_sign=hand["gyro_sign"], handedness_check_deg=hand["mean_angle_deg"],
                corr=corr.round(3).tolist(), first_left_turn_deg=abs(first_turn), segments=len(segs),
                nose_axis=nose["nose_axis"], nose_check_deg=nose["nose_check_deg"])


# --------------------------------------------------- page 4c: tilt comparison
def tilt_comparison(samples, cal: Calibration, bias_s: float = BIAS_S) -> dict:
    """Peak turn (axis only vs tilt fix) for a tilted_turn recording: mark 0 = arrived at 90."""
    t, r, bias = tilt_fixed_rate(samples, bias_s, cal.gravity_alpha, cal.gyro_sign)
    _, g = arrays(samples, GYRO)
    keep = t >= t[0] + bias_s
    t, r, g = t[keep], r[keep], g[keep] - bias
    fix = integrate(t, cal.sign * cal.scale * r)
    axis = integrate(t, cal.axis_sign * cal.scale * g[:, cal.yaw_axis])
    m = mark_times(samples)
    if len(m) == 0:
        raise RuntimeError("press Enter (MARK) when you reach the 90 deg line")
    i = np.searchsorted(t, m[0] + 0.5)
    note = None
    if {"yaw_axis", "axis_sign"} & set(cal.pending()):
        note = "axis-only column is meaningless until `analyze.py axis ... --save` has run"
    return dict(axis_only_deg=float(axis[i]), tilt_fix_deg=float(fix[i]), note=note)


# ------------------------------------------------------- page 5c: drift D1-D5
def drift_still(samples, cal: Optional[Calibration] = None, **kw) -> dict:
    """D1, D2, D4: worn/desk still. Linear fit of gyro-only yaw -> deg/min."""
    t, y = gyro_yaw(samples, cal, **kw)
    yu = unwrap(y)
    minutes = (t[-1] - t[0]) / 60.0
    slope = float(np.polyfit((t - t[0]) / 60.0, yu, 1)[0]) if len(t) > 2 else 0.0
    tg, g = arrays(samples, GYRO)
    noise = float(g[tg < tg[0] + BIAS_S].std(axis=0).max())
    return dict(minutes=minutes, drift_deg_per_min=slope, total_deg=float(yu[-1] - yu[0]),
                max_abs_deg=float(np.abs(yu - yu[0]).max()), still_noise_dps=noise,
                # auto re-bias needs the gyro quieter than this; 2x the worn-still noise leaves room for breathing
                suggested_rebias_max_std_dps=round(max(2.0 * noise, 0.3), 2))


def drift_at_marks(samples, expected_deg: Sequence[float] | float = 0.0, cal: Optional[Calibration] = None,
                   settle_s: float = 0.3, **kw) -> dict:
    """D3, D5: at each MARK the worker faces a tape line. Error vs where they faced."""
    t, y = gyro_yaw(samples, cal, **kw)
    m = mark_times(samples)
    exp = np.broadcast_to(np.asarray(expected_deg, float), m.shape)
    idx = np.clip(np.searchsorted(t, m + settle_s), 0, len(t) - 1)
    err = wrap(y[idx] - exp)
    return dict(mark_t=(m - t[0]).round(1).tolist(), error_deg=np.round(err, 2).tolist(),
                final_error_deg=float(err[-1]) if len(err) else None)


# ----------------------------------------------- page 6a: scale error (tape lines)
TAPE_ANGLES = (45, 90, 135, 180, -45, -90, -135, 0)


def scale_factor(samples, true_deg: Sequence[float] = TAPE_ANGLES, hold=(0.5, 2.5), cal=None) -> dict:
    """Average gyro yaw over each hold; fit true ~= k * measured (k multiplies the rate)."""
    t, y = gyro_yaw(samples, cal or Calibration(), auto_rebias=False)
    yu = unwrap(y)
    m = mark_times(samples)
    if len(m) != len(true_deg):
        raise RuntimeError(f"expected {len(true_deg)} marks, got {len(m)}")
    meas = np.array([yu[(t >= a + hold[0]) & (t < a + hold[1])].mean() for a in m])
    true = np.array(true_deg, float)
    # Measured is unwrapped along the path walked: 180 -> -45 is a 225 deg turn back, as walked.
    use = true != 0
    k = float((true[use] @ meas[use]) / (meas[use] @ meas[use]))
    _, g = arrays(samples, GYRO)
    clipped = int((np.abs(g) >= 0.98 * (cal or Calibration()).gyro_limit_dps).any(axis=1).sum())
    return dict(true_deg=true.tolist(), measured_deg=meas.round(2).tolist(), scale=k,
                return_to_zero_deg=float(meas[-1]), clipped_samples=clipped,
                warning="gyro clipped: turn slower and redo, the scale is unreliable" if clipped else None)


# ------------------------------------------------------ page 6b: fast turns
def fast_turns(samples, limit_dps: float = 245.0, expected=(90.0, 0.0), cal=None) -> dict:
    """Clipping and the yaw error it leaves. Marks alternate: at the line, back at 0."""
    tg, g = arrays(samples, GYRO)
    clipped = np.abs(g) >= 0.98 * limit_dps
    runs = _segments(clipped.any(axis=1), min_len=2)
    t, y = gyro_yaw(samples, cal, auto_rebias=False)
    m = mark_times(samples)
    exp = np.resize(np.asarray(expected, float), len(m))
    idx = np.clip(np.searchsorted(t, m + 0.5), 0, len(t) - 1)
    err = wrap(y[idx] - exp)
    return dict(peak_dps=float(np.abs(g).max()), clipped_samples=int(clipped.any(axis=1).sum()),
                flat_topped_peaks=len(runs), error_after_each_deg=np.round(err, 1).tolist(),
                final_error_deg=float(err[-1]) if len(err) else None)


# ---------------------------------------------------- page 6c: glance durations
def glance_durations(t, yaw, target_deg: float, tol_deg: float = 30.0) -> np.ndarray:
    """Seconds the head stays within +-tol of the target, per glance."""
    t, yaw = np.asarray(t, float), np.asarray(yaw, float)
    inside = np.abs(wrap(yaw - target_deg)) <= tol_deg
    return np.array([t[min(b, len(t) - 1)] - t[a] for a, b in _segments(inside, min_len=1)])


# --------------------------------------------------- page 9b: camera delay
def estimate_lag(samples, cal=None, max_lag_s: float = 0.3, fs: float = 100.0) -> dict:
    """Cross-correlate gyro turn rate with camera turn rate. Hanh owns the
    reference version; this is the same idea for checking against hers."""
    t, y = gyro_yaw(samples, cal, auto_rebias=False)
    tc, c = arrays(samples, CAM)
    if len(tc) < 10:
        raise RuntimeError("need camera frames")
    grid = np.arange(max(t[0], tc[0]), min(t[-1], tc[-1]), 1 / fs)
    rg = np.gradient(np.interp(grid, t, unwrap(y)), grid)
    rc = np.gradient(np.interp(grid, tc, median_filter(unwrap(c[:, 0]))), grid)
    rg, rc = rg - rg.mean(), rc - rc.mean()
    lags = np.arange(-int(max_lag_s * fs), int(max_lag_s * fs) + 1)
    xc = [np.dot(rg[max(0, -k):len(rg) - max(0, k)], rc[max(0, k):len(rc) - max(0, -k)]) for k in lags]
    xc = np.array(xc) / (np.linalg.norm(rg) * np.linalg.norm(rc))
    best = int(np.argmax(xc))
    return dict(lag_s=float(lags[best] / fs), peak_corr=float(xc[best]))


# ------------------------------------------------------- page 9d: tune K
def blend_sweep(samples, ks=(0.02, 0.05, 0.10, 0.20), hide=10.0, every=30.0, truth=None, cal=None) -> list[dict]:
    """For each K: error vs camera while visible, drift after `hide` s hidden, jitter when still.

    Hidden windows are cut out of the recording's camera frames (simulating the
    marker disappearing). Errors are measured against the camera itself unless
    `truth` (a function t -> yaw) is given, which only synthetic data has."""
    cal = cal or Calibration()
    tc, c = arrays(samples, CAM)
    t0 = tc[0] + 10.0
    windows = [(a, a + hide) for a in np.arange(t0, tc[-1] - hide, every)]
    hidden = lambda tt: any(a <= tt < b for a, b in windows)
    kept = [s for s in samples if not (s.kind == CAM and hidden(s.t))]
    rows = []
    for k in ks:
        c_k = Calibration(**{**cal.__dict__, "blend_k": k})
        est = HeadYawEstimator(c_k)
        out = run_offline(kept, est, reset_yaw=None)
        t = np.array([h.t_s for h in out])
        y = np.array([np.nan if h.yaw_deg is None else h.yaw_deg for h in out])
        rate = np.array([h.rate_dps for h in out])
        cam_ref = median_filter(unwrap(c[:, 0]))
        ref = (lambda tt: truth(tt)) if truth else (lambda tt: np.interp(tt + c_k.camera_lag_s, tc, cam_ref))
        vis = np.array([not hidden(tt) for tt in t]) & ~np.isnan(y) & (t > t0)
        err_vis = np.abs(wrap(y[vis] - ref(t[vis])))
        ends = [np.searchsorted(t, b) - 1 for _, b in windows]
        drift = [abs(float(wrap(y[i] - ref(t[i])))) for i in ends if 0 <= i < len(t)]
        # jitter: spread of yaw within each still, visible stretch of >= 1 s
        runs = _segments((np.abs(rate) < 3.0) & vis, min_len=52)
        jitter = float(np.median([np.std(unwrap(y[a:b])) for a, b in runs])) if runs else float("nan")
        rows.append(dict(K=k, err_visible_deg=float(np.median(err_vis)), err_visible_p95_deg=float(np.percentile(err_vis, 95)),
                         drift_after_hidden_deg=float(np.max(drift)) if drift else float("nan"),
                         jitter_still_deg=jitter))
    return rows


# ------------------------------------------------------ page 7b: Bluetooth
def bluetooth(samples) -> dict:
    tg, _ = arrays(samples, GYRO)
    td, n = arrays(samples, DROP)
    return gap_stats(tg, td, n[:, 0] if len(n) else ())
