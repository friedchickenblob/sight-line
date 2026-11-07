"""Gyro -> head yaw on the floor frame, ~50 Hz (pages 4, 5, 6d, 9).

Pipeline per gyro sample:
  raw w (deg/s, sensor axes)
    - bias                     measured standing still; re-zeroed when still 3 s (page 5a)
    * gyro_sign * scale        handedness + scale error, from calibration (pages 4a, 6a)
    rotate the orientation     full 3-D head orientation, tilt pulled toward the accelerometer (page 4b)
    yaw                        heading of the nose axis (page 4a calibration), or, until that is
                               measured, the rate about true vertical (w . up) integrated with dt
    * sign                     left turn positive (page 4a)
    skip gaps >= 0.1 s         a dropped packet is not a turn (page 5b)
    + camera pull K * err      complementary blend with Hanh's absolute yaw (page 9c), and
      bias -= Ki * err * up    ...the same error slowly re-learns the gyro bias about vertical

Pure helpers first, then the stateful HeadYawEstimator that owns all mutable state.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

import numpy as np

from .calibration import Calibration
from .frame import wrap
from .messages import HeadYaw

MAX_DT_S = 0.1           # larger gaps are dropped packets
MIN_LEVEL = 0.25         # nose heading is used while the nose is at most ~75 deg from level
STALE_S = 0.5            # no gyro for this long -> not valid (watchdog, page 7b)
BLEND_FRESH_S = 2.0      # source == "blend" while a camera correction is this recent
HIST_S = 1.5             # gyro yaw history kept for matching late camera frames
CLIP_TRUST_S = 1.0       # after the gyro clips, trust the camera harder for this long
CLIP_K = 0.2             # ...with this pull per frame (K=0.05 leaves ~20% of a clip error after 1 s)
CLIP_GATE_S = 0.15       # ...and accept a big (>45 deg) error after this long instead of gate_persist_s
GATE_FRAME_GAP_S = 0.2   # big-error frames further apart than this are not "the same" error


# --------------------------------------------------------------- pure helpers
def measure_bias(gyro_rows, max_std_dps: float = 2.0) -> np.ndarray:
    """Average gyro over a still window (rows of gx, gy, gz). Raises if they moved."""
    rows = np.asarray(gyro_rows, dtype=float)
    if len(rows) < 10:
        raise ValueError(f"need at least 10 gyro samples for a bias, got {len(rows)}")
    if rows.std(axis=0).max() > max_std_dps:
        raise RuntimeError("keep still during bias measurement")
    return rows.mean(axis=0)


def integrate(t, rate, max_dt: float = MAX_DT_S, yaw0: float = 0.0) -> np.ndarray:
    """Offline integration of a rate trace with LSL timestamps (unwrapped degrees)."""
    t, rate = np.asarray(t, float), np.asarray(rate, float)
    dt = np.diff(t, prepend=t[0])
    dt[(dt <= 0) | (dt >= max_dt)] = 0.0
    return yaw0 + np.cumsum(rate * dt)


def _rotvec(v) -> np.ndarray:
    """Rotation matrix for a rotation vector v (radians), by Rodrigues' formula."""
    angle = float(np.linalg.norm(v))
    if angle < 1e-12:
        return np.eye(3)
    k = np.asarray(v, float) / angle
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * (K @ K)


class Orientation:
    """Head orientation from gyro + accelerometer (pages 4b, 6d).

    R maps sensor axes to a floor-fixed frame whose z is up (from the
    accelerometer). Its x is arbitrary: there is no compass (page 2), so yaw
    is only ever relative to a reset or to Hanh's camera.
      propagate(): rotate R with the gyro every sample (fast, no lag),
      update():    tilt R slowly toward the accelerometer's "up" (fixes gyro
                   drift in tilt and ignores jolts, because alpha is small).
    `up` (floor z in sensor axes) is what the guide's slow accel average tracks;
    the guide's version alone lags a quick nod by ~1 s, and turns made during the
    nod get mis-projected. alpha 0.02 at 52 Hz is about a 1 s time constant.
    The first accel sample seeds the tilt directly.
    """

    def __init__(self, alpha: float = 0.02):
        self.alpha = alpha
        self.R: Optional[np.ndarray] = None
        self._n = 0

    @property
    def up(self) -> Optional[np.ndarray]:
        return None if self.R is None else self.R[2].copy()

    def propagate(self, w_dps, dt: float):
        """Body-frame rate w: R <- R exp([w dt]x)."""
        if self.R is None or not (0 < dt < MAX_DT_S):
            return
        self.R = self.R @ _rotvec(np.radians(np.asarray(w_dps, dtype=float)) * dt)
        self._n += 1
        if self._n % 50 == 0:                         # keep R a rotation despite rounding
            u, _, vt = np.linalg.svd(self.R)
            self.R = u @ vt

    def update(self, a) -> Optional[np.ndarray]:
        a = np.asarray(a, dtype=float)
        n = np.linalg.norm(a)
        if n < 1e-6:
            return self.up
        a = a / n
        if self.R is None:
            x = np.cross([0.0, 1.0, 0.0], a) if abs(a[1]) < 0.9 else np.cross([1.0, 0.0, 0.0], a)
            x /= np.linalg.norm(x)
            self.R = np.vstack([x, np.cross(a, x), a])   # rows: floor x, y, z in sensor axes
            return self.up
        # small body rotation d moves up toward a: up' = up + up x d, with d = alpha (a x up)
        self.R = self.R @ _rotvec(self.alpha * np.cross(a, self.R[2]))
        return self.up

    def heading(self, axis) -> tuple[float, float]:
        """Floor-frame heading (deg, CCW from floor x) of a sensor-fixed axis, and how
        horizontal that axis is (1 = level, 0 = pointing straight up or down)."""
        v = self.R @ np.asarray(axis, float)
        return float(np.degrees(np.arctan2(v[1], v[0]))), float(np.hypot(v[0], v[1]) / np.linalg.norm(v))


class StillRebias:
    """Re-zero the rate (not the angle) whenever the head has been still for 3 s (page 5).

    Guard: a real slow turn also has low jitter, so we only accept a new bias
    that is close to the old one (bias wanders slowly; turns do not look like
    bias). This keeps a slow, steady turn from being swallowed into the bias.
    """

    def __init__(self, window_s: float = 3.0, max_std_dps: float = 0.6, max_shift_dps: float = 0.75):
        self.window_s, self.max_std, self.max_shift = window_s, max_std_dps, max_shift_dps
        self._buf: deque = deque()
        self._next_check = -np.inf

    def update(self, t: float, w, bias: np.ndarray) -> Optional[np.ndarray]:
        self._buf.append((t, w))
        while self._buf and t - self._buf[0][0] > self.window_s:
            self._buf.popleft()
        if t < self._next_check or t - self._buf[0][0] < 0.95 * self.window_s:
            return None
        self._next_check = t + 0.5
        rows = np.array([x for _, x in self._buf])
        if rows.std(axis=0).max() > self.max_std:
            return None
        new = rows.mean(axis=0)
        if np.abs(new - bias).max() > self.max_shift:
            return None
        return new

    def clear(self):
        self._buf.clear()


class ReferenceGlance:
    """Fallback reset (page 6d): a sign on the wall at a known bearing. If the head
    is still and within `tol` of it for `hold_s`, snap yaw to the bearing. Fires
    once per dwell; must leave the tolerance cone to fire again."""

    def __init__(self, bearing_deg: float, tol_deg: float = 10.0, hold_s: float = 1.5, still_dps: float = 8.0):
        self.bearing, self.tol, self.hold_s, self.still = bearing_deg, tol_deg, hold_s, still_dps
        self._since: Optional[float] = None
        self._armed = True

    def update(self, t: float, yaw: float, rate: float) -> bool:
        inside = abs(wrap(yaw - self.bearing)) <= self.tol
        if not inside:
            self._since, self._armed = None, True
            return False
        if abs(rate) > self.still:
            self._since = None
            return False
        if self._since is None:
            self._since = t
        if self._armed and t - self._since >= self.hold_s:
            self._armed = False
            return True
        return False


# ------------------------------------------------------------------ estimator
class HeadYawEstimator:
    """Owns all head-yaw state. Feed it samples in time order; it returns HeadYaw.

        est = HeadYawEstimator(Calibration.load())
        est.set_bias(measure_bias(still_gyro_rows))
        est.reset(0.0)                    # start-of-run: worker faces +x, operator presses a key
        for s in batch:
            if s.kind == "ACC":  est.on_acc(s.t, s.v)
            if s.kind == "GYRO": msg = est.on_gyro(s.t, s.v)
            if s.kind == "CAM":  est.on_camera(s.t, s.v[0])
    """

    def __init__(self, cal: Calibration = None, *, use_camera: bool = True, auto_rebias: bool = True,
                 reference: Optional[ReferenceGlance] = None, gate_deg: float = 45.0, gate_persist_s: float = 1.0):
        self.cal = cal or Calibration()
        self.use_camera = use_camera
        self.orient = Orientation(self.cal.gravity_alpha)
        self.nose = None if self.cal.nose_axis is None else np.asarray(self.cal.nose_axis, float)
        self.rebias = StillRebias(max_std_dps=self.cal.rebias_max_std_dps) if auto_rebias else None
        self.reference = reference
        self.gate_deg, self.gate_persist_s = gate_deg, gate_persist_s
        self.bias: Optional[np.ndarray] = None
        self.yaw: Optional[float] = None
        self._offset: Optional[float] = None      # yaw = sign * nose heading + offset (nose mode)
        self.rate: float = 0.0
        self.clipped_samples = 0
        self.gaps = 0
        self.camera_rejected = 0
        self.rebias_count = 0
        self._last_t: Optional[float] = None
        self._last_cam_t: Optional[float] = None
        self._hist: deque = deque()
        self._big_err: Optional[tuple[float, float, float]] = None   # (since_t, err, last_t) for the 45 deg gate
        self._clip_until = -np.inf

    # -- run control
    def set_bias(self, bias):
        self.bias = np.asarray(bias, dtype=float)

    def reset(self, yaw_deg: float = 0.0):
        """Start-of-run reset: the worker faces a known direction (page 6d)."""
        self.yaw = wrap(yaw_deg)
        self._offset = None                         # re-anchored to the nose heading on the next gyro sample
        self._hist.clear()
        self._big_err = None

    # -- inputs
    def on_acc(self, t: float, a):
        self.orient.update(a)

    def on_gyro(self, t: float, w) -> HeadYaw:
        w = np.asarray(w, dtype=float)
        if np.any(np.abs(w) >= 0.98 * self.cal.gyro_limit_dps):     # raw counts clip, whatever the sign
            self.clipped_samples += 1
            self._clip_until = t + CLIP_TRUST_S
        dt = None if self._last_t is None else t - self._last_t
        self._last_t = t
        ready = self.bias is not None and self.orient.R is not None
        if not ready:
            return HeadYaw(t, self.yaw, 0.0, "gyro", self._cam_age(t), False)

        if self.rebias is not None:
            new = self.rebias.update(t, w, self.bias)
            if new is not None:
                self.bias = new
                self.rebias_count += 1

        gap = dt is not None and not (0 < dt < MAX_DT_S)
        w_true = self.cal.gyro_sign * self.cal.scale * (w - self.bias)   # handedness-corrected, deg/s
        if dt is not None and not gap:
            self.orient.propagate(w_true, dt)
        self.rate = self.cal.sign * float(np.dot(w_true, self.orient.up))
        if gap and dt is not None and dt > 0:
            self.gaps += 1
        if self.yaw is not None:
            self._advance(dt if dt is not None and not gap else None)
        if self.yaw is not None:
            self._hist.append((t, self.yaw))
            while self._hist and t - self._hist[0][0] > HIST_S:
                self._hist.popleft()
            if self.reference is not None and self.reference.update(t, self.yaw, self.rate):
                self.reset(self.reference.bearing)

        # Not valid while we know yaw is probably wrong and a fix is on its way:
        # a big camera disagreement awaiting the gate, or a clip with the camera in view.
        cam_age = self._cam_age(t)
        fixing_clip = t < self._clip_until and cam_age is not None and cam_age < 0.5
        valid = (self.yaw is not None and (dt is None or dt < STALE_S)
                 and self._big_err is None and not fixing_clip)
        return HeadYaw(t, self.yaw, self.rate, self._source(t), self._cam_age(t), valid)

    def on_camera(self, t_frame: float, yaw_cam: float) -> bool:
        """Pull toward Hanh's absolute yaw. Only call for frames where the marker
        read cleanly. Returns True if the frame was used."""
        if not self.use_camera:
            return False
        if self.yaw is None:
            self.reset(yaw_cam)
            self._last_cam_t = t_frame
            return True
        target_t = t_frame - self.cal.camera_lag_s
        then = self._yaw_at(target_t)
        if then is None:
            return False                # no gyro history near that moment: can't compare fairly
        err = wrap(yaw_cam - then)      # how wrong we were when that frame was taken
        clip_recovery = target_t < self._clip_until
        if abs(err) > self.gate_deg:
            # Probably a misread. Accept only if the same big error persists over
            # consecutive frames for ~1 s (the worker really turned while the marker
            # was hidden), or ~0.15 s right after a clip (we know the gyro lost angle).
            b = self._big_err
            if b is None or t_frame - b[2] > GATE_FRAME_GAP_S or abs(wrap(err - b[1])) > 15.0:
                self._big_err = (t_frame, err, t_frame)
                self.camera_rejected += 1
                return False
            self._big_err = (b[0], err, t_frame)
            if t_frame - b[0] < (CLIP_GATE_S if clip_recovery else self.gate_persist_s):
                self.camera_rejected += 1
                return False
            self._big_err = None
            self._shift(err)            # trust it fully: we were simply wrong
            self._last_cam_t = t_frame
            return True
        self._big_err = None
        k = max(CLIP_K, self.cal.blend_k) if clip_recovery else self.cal.blend_k
        self._shift(k * err)
        if not clip_recovery and self.cal.blend_ki and self.bias is not None and self.orient.R is not None:
            # A steady error means the bias about vertical is off. Nudge the bias vector
            # along "up" so the rate (sign * gyro_sign * scale * (w - bias) . up) grows by Ki * err.
            step = self.cal.blend_ki * err * self.cal.sign * self.cal.gyro_sign / self.cal.scale
            self.bias = self.bias - step * self.orient.up
        self._last_cam_t = t_frame
        return True

    # -- internals
    def _yaw_at(self, t: float) -> Optional[float]:
        """Our yaw at time t, interpolated from history (None if not covered)."""
        if not self._hist:
            return self.yaw
        h = self._hist
        if t <= h[0][0]:
            return h[0][1] if h[0][0] - t <= 0.1 else None
        if t >= h[-1][0]:
            return h[-1][1] if t - h[-1][0] <= 0.1 else None
        i = next(k for k in range(1, len(h)) if h[k][0] >= t)
        (t0, y0), (t1, y1) = h[i - 1], h[i]
        return wrap(y0 + wrap(y1 - y0) * (t - t0) / (t1 - t0)) if t1 > t0 else y1

    def _advance(self, dt: Optional[float]):
        """Move yaw on by one gyro sample. dt None = gap or first sample: hold."""
        if self.nose is None:
            if dt is not None:
                self.yaw = wrap(self.yaw + self.rate * dt)
            return
        heading, level = self.orient.heading(self.nose)
        if self._offset is None:
            self._offset = self.yaw - self.cal.sign * heading
        elif dt is not None:
            if level >= MIN_LEVEL:
                self.yaw = wrap(self.cal.sign * heading + self._offset)
            else:
                # nose near vertical: heading is ill-defined, so integrate and keep the offset continuous
                self.yaw = wrap(self.yaw + self.rate * dt)
                self._offset = self.yaw - self.cal.sign * heading

    def _shift(self, delta: float):
        self.yaw = wrap(self.yaw + delta)
        if self._offset is not None:
            self._offset += delta
        self._hist = deque((t, wrap(y + delta)) for t, y in self._hist)

    def _cam_age(self, t: float) -> Optional[float]:
        return None if self._last_cam_t is None else t - self._last_cam_t

    def _source(self, t: float) -> str:
        age = self._cam_age(t)
        return "blend" if age is not None and age < BLEND_FRESH_S else "gyro"


class YawPipeline:
    """The one loop shared by live, replay and offline analysis, so they can't drift apart.

    Feed every Sample in time order. Handles the bias window (first `bias_window_s`
    of gyro, camera ignored meanwhile), the start-of-run reset, RESET samples (the
    Enter key in live_yaw.py, recorded so replay re-zeros at the same moment), and
    routes ACC/GYRO/CAM to the estimator. Returns a HeadYaw per gyro sample once running.
    """

    def __init__(self, est: HeadYawEstimator, bias_window_s: float = 5.0, start_yaw: Optional[float] = 0.0,
                 retry_bias: bool = False):
        self.est, self.window, self.start_yaw, self.retry = est, bias_window_s, start_yaw, retry_bias
        self._still: list = []
        self._t0: Optional[float] = None
        self.events: list[str] = []          # human-readable notes for a UI

    @property
    def running(self) -> bool:
        return self.est.bias is not None

    def feed(self, s) -> Optional[HeadYaw]:
        if s.kind == "ACC":
            self.est.on_acc(s.t, s.v)
            return None
        if not self.running:
            if s.kind == "GYRO":
                self._t0 = s.t if self._t0 is None else self._t0
                if s.t - self._t0 < self.window:
                    self._still.append(s.v)
                    return None
                try:
                    self.est.set_bias(measure_bias(self._still))
                except (RuntimeError, ValueError) as e:
                    if not self.retry:
                        raise
                    self.events.append(f"{e}; measuring bias again")
                    self._still, self._t0 = [], None
                    return None
                if self.start_yaw is not None:
                    self.est.reset(self.start_yaw)
                self.events.append(f"bias {np.round(self.est.bias, 2)} deg/s")
            else:
                return None
        if s.kind == "GYRO":
            return self.est.on_gyro(s.t, s.v)
        if s.kind == "CAM":
            self.est.on_camera(s.t, s.v[0])
        elif s.kind == "RESET":
            self.est.reset(s.v[0] if s.v else 0.0)
            self.events.append("re-zeroed")
        return None


def run_offline(samples, est: HeadYawEstimator, bias_window_s: float = 5.0, reset_yaw: Optional[float] = 0.0):
    """Run a whole recording through YawPipeline. Returns one HeadYaw per gyro
    sample after the bias window. Raises if they moved during the bias window."""
    samples = sorted(samples, key=lambda s: s.t)
    if not any(s.kind == "GYRO" for s in samples):
        raise ValueError("recording has no GYRO samples")
    pipe = YawPipeline(est, bias_window_s, reset_yaw)
    return [h for h in map(pipe.feed, samples) if h is not None]
