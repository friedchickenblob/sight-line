"""Head-orientation estimation from the Muse accelerometer + gyroscope.

The Muse IMU has no magnetometer, so absolute (compass) heading is not
observable. What we can do:

* pitch / roll: absolute, corrected continuously by gravity.
* heading (yaw): relative to a "tare" reference, from integrated gyro.
  It drifts slowly; gyro bias calibration (and optional auto re-estimation
  while the head is still) keeps the drift small, and `tare()` re-zeroes it.

Conventions (world frame): z = up (direction of the measured gravity
reaction at calibration). Heading follows the project convention (CLAUDE.md):
degrees, counter-clockwise positive seen from above (= head turned LEFT),
wrapped to [-180, 180); tare while facing the floor +x direction so 0 deg = +x.
Pitch positive = looking UP, roll positive = right ear DOWN.

The heading estimate does not depend on how the sensor is mounted, because
it is a rotation about the measured gravity axis. Pitch/roll need to know
which sensor axis points forward (`forward_axis`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

DEG2RAD = math.pi / 180.0
RAD2DEG = 180.0 / math.pi

AXES = {
    "+x": (1, 0, 0), "-x": (-1, 0, 0),
    "+y": (0, 1, 0), "-y": (0, -1, 0),
    "+z": (0, 0, 1), "-z": (0, 0, -1),
}


# --------------------------------------------------------------------------- quaternion helpers
def quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ])


def quat_rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rotate body-frame vector v into the world frame using q (body->world)."""
    w, x, y, z = q
    r = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])
    return r @ v


def quat_from_two_vectors(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Shortest-arc quaternion rotating unit vector src onto unit vector dst."""
    src = src / np.linalg.norm(src)
    dst = dst / np.linalg.norm(dst)
    d = float(np.dot(src, dst))
    if d < -0.999999:
        # 180 deg: pick any orthogonal axis
        axis = np.cross([1.0, 0, 0], src)
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross([0, 1.0, 0], src)
        axis /= np.linalg.norm(axis)
        return np.array([0.0, *axis])
    c = np.cross(src, dst)
    q = np.array([1.0 + d, *c])
    return q / np.linalg.norm(q)


def wrap180(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


# --------------------------------------------------------------------------- Madgwick (IMU only)
class MadgwickIMU:
    """Madgwick gradient-descent AHRS, 6-DOF variant (gyro + accel).

    q maps body -> world; world z is aligned with the measured accel vector.
    """

    def __init__(self, beta: float = 0.04, q0: np.ndarray | None = None):
        self.beta = beta
        self.q = np.array([1.0, 0, 0, 0]) if q0 is None else np.asarray(q0, float)

    def update(self, gyro_rad: np.ndarray, accel: np.ndarray, dt: float) -> np.ndarray:
        q1, q2, q3, q4 = self.q
        gx, gy, gz = gyro_rad
        q_dot = 0.5 * quat_mul(self.q, np.array([0.0, gx, gy, gz]))

        norm_a = np.linalg.norm(accel)
        if norm_a > 1e-9:
            ax, ay, az = accel / norm_a
            f = np.array([
                2 * (q2 * q4 - q1 * q3) - ax,
                2 * (q1 * q2 + q3 * q4) - ay,
                2 * (0.5 - q2 * q2 - q3 * q3) - az,
            ])
            j = np.array([
                [-2 * q3, 2 * q4, -2 * q1, 2 * q2],
                [2 * q2, 2 * q1, 2 * q4, 2 * q3],
                [0, -4 * q2, -4 * q3, 0],
            ])
            step = j.T @ f
            n = np.linalg.norm(step)
            if n > 1e-12:
                q_dot -= self.beta * step / n

        self.q = self.q + q_dot * dt
        self.q /= np.linalg.norm(self.q)
        return self.q


# --------------------------------------------------------------------------- calibration
@dataclass
class StillCalibration:
    gyro_bias_dps: np.ndarray        # deg/s, sensor frame
    up_body: np.ndarray              # unit vector, sensor frame (mean accel direction)
    gyro_std_dps: float
    accel_norm_std_g: float
    n_samples: int

    @property
    def is_still(self) -> bool:
        return self.gyro_std_dps < STILL_GYRO_STD_DPS and self.accel_norm_std_g < STILL_ACCEL_STD_G


STILL_GYRO_STD_DPS = 2.0
STILL_ACCEL_STD_G = 0.03


def calibrate_still(accel_g: np.ndarray, gyro_dps: np.ndarray) -> StillCalibration:
    """accel_g, gyro_dps: arrays of shape (N, 3) recorded while the head is still."""
    accel_g = np.asarray(accel_g, float)
    gyro_dps = np.asarray(gyro_dps, float)
    if len(accel_g) < 10:
        raise ValueError(f"need >=10 IMU samples to calibrate, got {len(accel_g)}")
    mean_a = accel_g.mean(axis=0)
    return StillCalibration(
        gyro_bias_dps=gyro_dps.mean(axis=0),
        up_body=mean_a / np.linalg.norm(mean_a),
        gyro_std_dps=float(np.linalg.norm(gyro_dps.std(axis=0))),
        accel_norm_std_g=float(np.linalg.norm(accel_g, axis=1).std()),
        n_samples=len(accel_g),
    )


# --------------------------------------------------------------------------- tracker
@dataclass
class OrientationSample:
    heading_deg: float   # relative to tare, + = turned left (CCW from above)
    pitch_deg: float     # + = looking up
    roll_deg: float      # + = right ear down
    still: bool
    quat: np.ndarray = field(repr=False)


class HeadOrientationTracker:
    def __init__(
        self,
        calibration: StillCalibration,
        sample_rate_hz: float,
        forward_axis: str = "+x",
        beta: float = 0.04,
        invert_heading: bool = False,
        auto_bias: bool = False,
        auto_bias_tau_s: float = 20.0,
    ):
        self.cal = calibration
        self.dt = 1.0 / sample_rate_hz
        self.bias = calibration.gyro_bias_dps.copy()
        self.invert_heading = invert_heading
        self.auto_bias = auto_bias
        self.auto_bias_alpha = self.dt / max(auto_bias_tau_s, self.dt)

        up = calibration.up_body
        fwd = np.asarray(AXES[forward_axis], float)
        fwd = fwd - np.dot(fwd, up) * up          # make forward horizontal at calibration
        if np.linalg.norm(fwd) < 0.3:
            raise ValueError(
                f"forward axis {forward_axis} is (nearly) vertical while worn; pick a horizontal axis"
            )
        self.fwd_body = fwd / np.linalg.norm(fwd)
        self.right_body = np.cross(self.fwd_body, up)

        # initialise attitude straight from gravity: no convergence wait
        self.filter = MadgwickIMU(beta=beta, q0=quat_from_two_vectors(up, np.array([0, 0, 1.0])))
        self._heading_ref = 0.0
        self._heading_raw_unwrapped = None
        self._last_raw = None
        self._still_count = 0
        self.tare()

    # raw heading of the forward axis around world z, unwrapped, + = CCW (left turn)
    def _raw_heading(self) -> float:
        f = quat_rotate(self.filter.q, self.fwd_body)
        h = math.atan2(f[1], f[0]) * RAD2DEG
        if self.invert_heading:
            h = -h
        if self._last_raw is None:
            self._heading_raw_unwrapped = h
        else:
            self._heading_raw_unwrapped += wrap180(h - self._last_raw)
        self._last_raw = h
        return self._heading_raw_unwrapped

    def tare(self) -> None:
        """Define the current facing direction as heading 0."""
        self._heading_ref = self._raw_heading()

    def update(self, accel_g: np.ndarray, gyro_dps: np.ndarray) -> OrientationSample:
        accel_g = np.asarray(accel_g, float)
        gyro_dps = np.asarray(gyro_dps, float)
        corrected = gyro_dps - self.bias

        still = (np.linalg.norm(corrected) < STILL_GYRO_STD_DPS * 1.5
                 and abs(np.linalg.norm(accel_g) - 1.0) < 0.05)
        self._still_count = self._still_count + 1 if still else 0
        # only trust stillness after ~1 s of it, then slowly re-estimate bias
        settled = self._still_count * self.dt > 1.0
        if self.auto_bias and settled:
            self.bias += self.auto_bias_alpha * (gyro_dps - self.bias)
            corrected = gyro_dps - self.bias

        q = self.filter.update(corrected * DEG2RAD, accel_g, self.dt)

        heading = self._raw_heading() - self._heading_ref
        f = quat_rotate(q, self.fwd_body)
        r = quat_rotate(q, self.right_body)
        pitch = math.asin(max(-1.0, min(1.0, f[2]))) * RAD2DEG
        roll = -math.asin(max(-1.0, min(1.0, r[2]))) * RAD2DEG
        return OrientationSample(wrap180(heading), pitch, roll, settled, q.copy())
