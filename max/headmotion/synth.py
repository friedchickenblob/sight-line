"""Fake Muse + fake camera, so every algorithm can be tested before hardware.

The model:
  * Head orientation R(t) = Rz(yaw) Ry(pitch) Rx(roll) maps head axes
    (x forward, y left, z up) to the floor frame. Positive pitch = looking down.
  * The Muse's axes are a fixed rotation M of the head axes (unknown in real
    life, which is why page 4a exists). The default M puts yaw on -gy so the
    axis/sign experiment has something to find.
  * Gyro = scale * (body angular velocity) + bias(t) + noise, clipped at the limit.
  * Accel = gravity reaction (+1 g "up") + head-on-neck lever acceleration + noise.
  * Samples at ~52 Hz with small timestamp jitter and dropped 3-sample packets
    (each run of lost packets is announced by a DROP sample, as sources.PacketDecoder does).
  * Camera = true yaw seen LAG seconds ago, 30 fps, jitter, hidden windows, misreads.

A script is a list of moves; each move eases (minimum-jerk) from the current
pose to a target pose over `dur` seconds, then optionally holds.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .frame import wrap
from .sources import ACC, CAM, DROP, GYRO, MARK, Sample

# Sensor axes as rows, written in head axes. Proper rotation (det +1).
# gx = -head y, gy = -head z, gz = head x: so a left turn shows up as NEGATIVE gy.
DEFAULT_MOUNT = np.array([[0.0, -1.0, 0.0],
                          [0.0, 0.0, -1.0],
                          [1.0, 0.0, 0.0]])
assert abs(np.linalg.det(DEFAULT_MOUNT) - 1.0) < 1e-9


@dataclass
class Move:
    dur: float                       # seconds to reach the target
    yaw: float | None = None         # target, degrees (None = keep)
    pitch: float | None = None
    roll: float | None = None
    hold: float = 0.0                # seconds to hold afterwards
    mark: bool = False               # emit a MARK sample at the start of the hold (like pressing Enter)


@dataclass
class ImuConfig:
    fs: float = 52.0
    bias_dps: tuple = (0.4, -0.25, 0.3)      # constant gyro bias per sensor axis
    bias_drift_dps_per_min: tuple = (0.0, 0.0, 0.0)
    noise_dps: float = 0.15
    scale: float = 1.0                         # e.g. 1.033 = 90 deg reads 93
    limit_dps: float = 245.0
    acc_noise_g: float = 0.01
    lever_m: tuple = (0.05, 0.0, 0.10)         # sensor offset from the neck pivot (head axes):
                                               # turning adds centripetal/tangential accel, as on a real head
    jitter_s: float = 0.002
    drop_packet_prob: float = 0.0              # per 3-sample packet
    mount: np.ndarray = field(default_factory=lambda: DEFAULT_MOUNT.copy())
    gyro_sign: float = 1.0                     # -1 = gyro mirrored relative to accel (e.g. Athena per BlueMuse)
    seed: int = 0


@dataclass
class CameraConfig:
    fps: float = 30.0
    lag_s: float = 0.10
    noise_deg: float = 1.0
    hidden: tuple = ()                         # ((t0, t1), ...) marker not visible
    misread_prob: float = 0.0                  # random wild readings
    seed: int = 1


@dataclass
class Truth:
    t: np.ndarray
    yaw: np.ndarray      # degrees, unwrapped
    pitch: np.ndarray
    roll: np.ndarray


def _min_jerk(s):
    return 10 * s**3 - 15 * s**4 + 6 * s**5


def run_script(moves: list[Move], fs: float = 1040.0, start=(0.0, 0.0, 0.0)) -> tuple[Truth, list[float]]:
    """Dense ground-truth pose trace + the times of any marks."""
    ts, ys, ps, rs, marks = [0.0], [start[0]], [start[1]], [start[2]], []
    t, cur = 0.0, list(start)
    for m in moves:
        tgt = [cur[0] if m.yaw is None else m.yaw,
               cur[1] if m.pitch is None else m.pitch,
               cur[2] if m.roll is None else m.roll]
        n = max(int(round(m.dur * fs)), 1)
        s = _min_jerk(np.arange(1, n + 1) / n)
        for k in range(3):
            [ys, ps, rs][k].extend(cur[k] + (tgt[k] - cur[k]) * s)
        ts.extend(t + np.arange(1, n + 1) / fs)
        t += n / fs
        cur = tgt
        if m.mark:
            marks.append(t)
        nh = int(round(m.hold * fs))
        if nh:
            for k in range(3):
                [ys, ps, rs][k].extend([cur[k]] * nh)
            ts.extend(t + np.arange(1, nh + 1) / fs)
            t += nh / fs
    return Truth(np.array(ts), np.array(ys), np.array(ps), np.array(rs)), marks


def _rot(yaw, pitch, roll):
    """Stack of 3x3 rotation matrices head -> floor, angles in degrees."""
    y, p, r = (np.radians(a) for a in (yaw, pitch, roll))
    cy, sy, cp, sp, cr, sr = np.cos(y), np.sin(y), np.cos(p), np.sin(p), np.cos(r), np.sin(r)
    R = np.empty((len(y), 3, 3))
    R[:, 0, 0] = cy * cp; R[:, 0, 1] = cy * sp * sr - sy * cr; R[:, 0, 2] = cy * sp * cr + sy * sr
    R[:, 1, 0] = sy * cp; R[:, 1, 1] = sy * sp * sr + cy * cr; R[:, 1, 2] = sy * sp * cr - cy * sr
    R[:, 2, 0] = -sp;     R[:, 2, 1] = cp * sr;                R[:, 2, 2] = cp * cr
    return R


def body_rates(truth: Truth) -> np.ndarray:
    """Angular velocity in head axes (deg/s) at each dense sample."""
    R = _rot(truth.yaw, truth.pitch, truth.roll)
    dt = np.gradient(truth.t)
    Rdot = np.gradient(R, axis=0) / dt[:, None, None]
    S = np.einsum("nji,njk->nik", R, Rdot)          # R^T Rdot, skew-symmetric
    w = np.stack([S[:, 2, 1] - S[:, 1, 2], S[:, 0, 2] - S[:, 2, 0], S[:, 1, 0] - S[:, 0, 1]], axis=1) / 2
    return np.degrees(w)


def imu_samples(truth: Truth, cfg: ImuConfig = ImuConfig(), t0: float = 0.0) -> list[Sample]:
    rng = np.random.default_rng(cfg.seed)
    t_nom = np.arange(truth.t[0], truth.t[-1], 1.0 / cfg.fs)
    keep = np.ones(len(t_nom), bool)
    if cfg.drop_packet_prob > 0:
        for i in range(0, len(t_nom), 3):
            if rng.random() < cfg.drop_packet_prob:
                keep[i:i + 3] = False
    # samples lost just before each kept sample (what the packet counter reveals)
    lost_before = np.diff(np.r_[-1, np.flatnonzero(keep)]) - 1
    t_nom = t_nom[keep]
    w_dense = body_rates(truth)
    w_rad = np.radians(w_dense)
    alpha = np.gradient(w_rad, truth.t, axis=0)
    r = np.asarray(cfg.lever_m, float)
    a_lin = np.cross(alpha, r) + np.cross(w_rad, np.cross(w_rad, r))     # m/s^2, head axes
    w_head = np.stack([np.interp(t_nom, truth.t, w_dense[:, k]) for k in range(3)], axis=1)
    a_lin = np.stack([np.interp(t_nom, truth.t, a_lin[:, k]) for k in range(3)], axis=1) / 9.81
    R = _rot(*(np.interp(t_nom, truth.t, a) for a in (truth.yaw, truth.pitch, truth.roll)))
    up_head = R[:, 2, :]                             # floor z written in head axes = R^T e_z
    M = cfg.mount
    bias = np.asarray(cfg.bias_dps) + np.outer(t_nom / 60.0, cfg.bias_drift_dps_per_min)
    gyro = cfg.gyro_sign * cfg.scale * w_head @ M.T + bias + rng.normal(0, cfg.noise_dps, (len(t_nom), 3))
    gyro = np.clip(gyro, -cfg.limit_dps, cfg.limit_dps)
    acc = (up_head + a_lin) @ M.T + rng.normal(0, cfg.acc_noise_g, (len(t_nom), 3))
    t_stamp = t0 + t_nom + rng.normal(0, cfg.jitter_s, len(t_nom))
    t_stamp = np.maximum.accumulate(t_stamp)
    out = []
    for i, t in enumerate(t_stamp):
        if lost_before[i]:
            out.append(Sample(DROP, float(t), (float(lost_before[i]),)))
        out.append(Sample(GYRO, float(t), tuple(gyro[i])))
        out.append(Sample(ACC, float(t), tuple(acc[i])))
    return out


def camera_samples(truth: Truth, cfg: CameraConfig = CameraConfig(), t0: float = 0.0) -> list[Sample]:
    rng = np.random.default_rng(cfg.seed)
    t = np.arange(truth.t[0] + cfg.lag_s, truth.t[-1], 1.0 / cfg.fps)
    yaw = np.interp(t - cfg.lag_s, truth.t, truth.yaw) + rng.normal(0, cfg.noise_deg, len(t))
    out = []
    for ti, yi in zip(t, yaw):
        if any(a <= ti < b for a, b in cfg.hidden):
            continue
        if cfg.misread_prob and rng.random() < cfg.misread_prob:
            yi = yi + rng.choice([-1, 1]) * rng.uniform(60, 180)
        out.append(Sample(CAM, float(t0 + ti), (float(wrap(yi)),)))
    return out


def mark_samples(marks: list[float], t0: float = 0.0) -> list[Sample]:
    return [Sample(MARK, float(t0 + t), (float(i),)) for i, t in enumerate(marks)]


def truth_yaw_at(truth: Truth, t) -> np.ndarray:
    return wrap(np.interp(t, truth.t, truth.yaw))


# ----------------------------------------------------------- ready-made scripts
def still(seconds: float) -> list[Move]:
    return [Move(dur=0.01, hold=seconds)]


def axis_experiment() -> list[Move]:
    """Page 4a: 3 x left 90, 3 x right 90, 3 x nod, 3 x ear-to-shoulder, with pauses."""
    m = still(5.0)
    for _ in range(3):
        m += [Move(0.8, yaw=90, hold=0.5), Move(0.8, yaw=0, hold=1.0)]
    for _ in range(3):
        m += [Move(0.8, yaw=-90, hold=0.5), Move(0.8, yaw=0, hold=1.0)]
    for _ in range(3):
        m += [Move(0.5, pitch=30, hold=0.3), Move(0.5, pitch=0, hold=1.0)]
    for _ in range(3):
        m += [Move(0.5, roll=25, hold=0.3), Move(0.5, roll=0, hold=1.0)]
    return m


def tilted_turn(pitch: float = 0.0, roll: float = 0.0) -> list[Move]:
    """Page 4c: settle into a head pose, turn 90 left, hold, come back."""
    return still(5.0) + [Move(1.0, pitch=pitch, roll=roll, hold=1.0),
                         Move(1.2, yaw=90, hold=2.0, mark=True),
                         Move(1.2, yaw=0, hold=2.0, mark=True)]


def tape_lines() -> list[Move]:
    """Page 6a: visit each tape line, hold 3 s (mark on arrival), return to 0.
    Turns are slow (peak ~120 deg/s) so clipping can't spoil the scale estimate."""
    m, cur = still(5.0), 0.0
    for a in (45, 90, 135, 180, -45, -90, -135, 0):
        a = a if a != 180 else 179.9
        m.append(Move(max(1.5, 1.875 * abs(a - cur) / 120.0), yaw=a, hold=3.0, mark=True))
        cur = a
    return m


def fast_turns(n: int = 10, peak_s: float = 0.3) -> list[Move]:
    """Page 6b: snap to a tape line at 90 and back, as fast as possible."""
    m = still(5.0)
    for _ in range(n):
        m += [Move(peak_s, yaw=90, hold=1.5, mark=True), Move(peak_s, yaw=0, hold=1.5, mark=True)]
    return m


def glances(target: float = 60.0, n: int = 10, dwell_s=(0.15, 0.6), seed: int = 3) -> list[Move]:
    """Page 6c: glance at a target and back. Kept below the clip level (~170 deg/s
    peak for 60 deg); real snap glances are faster, see fast_turns()."""
    rng = np.random.default_rng(seed)
    m = still(5.0)
    for _ in range(n):
        m += [Move(rng.uniform(0.5, 0.7), yaw=target, hold=float(rng.uniform(*dwell_s))),
              Move(rng.uniform(0.5, 0.7), yaw=0, hold=float(rng.uniform(1.0, 2.0)))]
    return m


def free_movement(seconds: float = 120.0, seed: int = 4, max_rate_dps: float = 200) -> list[Move]:
    """Page 9a: random looking around, with some head pitch."""
    rng = np.random.default_rng(seed)
    m, t, yaw = still(5.0), 5.0, 0.0
    while t < seconds:
        step = float(rng.uniform(-120, 120))
        dur = max(float(rng.uniform(0.6, 2.0)), 1.875 * abs(step) / max_rate_dps)
        yaw += step
        m.append(Move(dur, yaw=yaw, pitch=float(rng.uniform(-10, 40)), hold=float(rng.uniform(0.2, 2.0))))
        t += dur + m[-1].hold
    return m


# ------------------------------------------------- named scenes for the scripts
def scene(name: str, seed: int = 0) -> list[Sample]:
    """Ready-made fake recordings, one per experiment in the guide.
    Use as `--source synthetic:<name>` in any script."""
    imu, cam = ImuConfig(seed=seed), None
    if name == "axis":                       # page 4a
        moves = axis_experiment()
    elif name.startswith("tilt"):            # page 4c: tilt, tilt-down, tilt-up, tilt-side
        pose = {"tilt": (0, 0), "tilt-down": (40, 0), "tilt-up": (-20, 0), "tilt-side": (0, 20)}[name]
        moves = tilted_turn(*pose)
    elif name == "desk":                     # D1: 10 min still
        moves, imu.lever_m = still(600), (0, 0, 0)
    elif name == "worn-still":               # D2: 5 min, bias creeping as it warms against skin
        moves, imu.bias_drift_dps_per_min = still(300), (0.0, -0.1, 0.0)
    elif name == "worn-active":              # D3: 2 min, face the tape mark (0 deg) every 30 s
        moves = still(5)
        for k in range(4):
            fm = free_movement(30, seed=seed + k)[1:]
            end = run_script(moves + fm)[0].yaw[-1]
            moves += fm + [Move(1.5, yaw=end + wrap(0 - end), hold=2.0, mark=True)]
    elif name == "tape":                     # page 6a, with a 3% scale error to find
        moves, imu.scale = tape_lines(), 1.033
    elif name == "fast":                     # page 6b
        moves = fast_turns()
    elif name == "glances":                  # page 6c
        moves = glances()
    elif name == "joint":                    # page 9a: still, 10 slow, 10 fast, 2 min free; with camera
        moves = still(5)
        for _ in range(10):
            moves += [Move(1.5, yaw=90, hold=0.5), Move(1.5, yaw=0, hold=0.5)]
        for _ in range(10):
            moves += [Move(0.35, yaw=90, hold=0.5), Move(0.35, yaw=0, hold=0.5)]
        moves += free_movement(120, seed=seed)[1:]
        imu.bias_drift_dps_per_min = (0.0, -0.3, 0.0)
        cam = CameraConfig(lag_s=0.10, misread_prob=0.01, seed=seed + 1)
    elif name == "bluetooth":                # page 7b: 2 min with 2% dropped packets
        moves, imu.drop_packet_prob = still(120), 0.02
    else:
        raise ValueError(f"unknown synthetic scene {name!r}; try one of {SCENE_NAMES}")
    truth, marks = run_script(moves)
    out = imu_samples(truth, imu) + mark_samples(marks)
    if cam is not None:
        out += camera_samples(truth, cam)
    return sorted(out, key=lambda s: s.t)


SCENE_NAMES = ("axis", "tilt", "tilt-down", "tilt-up", "tilt-side", "desk", "worn-still", "worn-active",
               "tape", "fast", "glances", "joint", "bluetooth")
