"""Tiny contact simulator (page 8): synthetic machine/worker paths at 30 Hz with
+-2 cm tracker noise, run through ContactTracker frame by frame.

Each scene lists what the flag SHOULD do so tests and the plot script can
check it automatically.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .contact import ContactTracker, estimate_velocity

FPS = 30.0
NOISE_M = 0.02


@dataclass
class Scene:
    name: str
    expect: str
    t: np.ndarray
    worker: np.ndarray     # (n, 2) true positions
    machine: np.ndarray    # (n, 2)


def _line(t, start, vel, stop_t=None):
    start, vel = np.asarray(start, float), np.asarray(vel, float)
    tt = t if stop_t is None else np.minimum(t, stop_t)
    return start + np.outer(tt, vel)


def straight_at(speed=0.8, start_d=3.0, R=0.5) -> Scene:
    dur = (start_d - R) / speed + 0.2           # end just after contact
    t = np.arange(0, dur, 1 / FPS)
    return Scene("straight at worker, 0.8 m/s", "on once ~T s before contact, stays on",
                 t, _line(t, (0, 0), (0, 0)), _line(t, (start_d, 0), (-speed, 0)))


def pass_by(clearance, speed=0.8, half_len=2.5) -> Scene:
    t = np.arange(0, 2 * half_len / speed, 1 / FPS)
    return Scene(f"passing with {clearance} m clearance",
                 "never on" if clearance > 0.5 else "on (near miss)",
                 t, _line(t, (0, 0), (0, 0)), _line(t, (half_len, clearance), (-speed, 0)))


def stop_short(speed=0.8, start_d=3.0, stop_at=1.0) -> Scene:
    t_stop = (start_d - stop_at) / speed
    t = np.arange(0, t_stop + 3.0, 1 / FPS)
    return Scene("heading in, stops 1 m short", "on, then off once stopped",
                 t, _line(t, (0, 0), (0, 0)), _line(t, (start_d, 0), (-speed, 0), stop_t=t_stop))


def worker_walks(clearance, speed=0.6, half_len=2.0) -> Scene:
    t = np.arange(0, 2 * half_len / speed, 1 / FPS)
    return Scene(f"worker walks past parked machine, {clearance} m clearance",
                 "never on" if clearance > 0.5 else "on (walks into it)",
                 t, _line(t, (-half_len, clearance), (speed, 0)), _line(t, (0, 0), (0, 0)))


def all_scenes() -> list[Scene]:
    return [straight_at(), pass_by(1.2), pass_by(0.3), stop_short(), worker_walks(0.3), worker_walks(1.0)]


def run_scene(scene: Scene, R=0.5, T=2.0, noise_m=NOISE_M, vel_window_s=0.5, seed=0, hysteresis=True):
    """Returns dict of arrays: t, d, ttc (nan = None), raw, flag."""
    rng = np.random.default_rng(seed)
    w = scene.worker + rng.uniform(-noise_m, noise_m, scene.worker.shape)
    m = scene.machine + rng.uniform(-noise_m, noise_m, scene.machine.shape)
    trk = ContactTracker(R, T) if hysteresis else ContactTracker(R, T, off_margin_s=0.0, hold_s=0.0)
    out = {k: [] for k in ("t", "d", "ttc", "raw", "flag", "d_min")}
    for i, t in enumerate(scene.t):
        sl = slice(0, i + 1)
        vm = estimate_velocity(scene.t[sl], m[sl], vel_window_s)
        vw = estimate_velocity(scene.t[sl], w[sl], vel_window_s)
        msg = trk.update(t, w[i], m[i], vm, vw)
        out["t"].append(t); out["d"].append(msg.d_m); out["d_min"].append(msg.d_min_m)
        out["ttc"].append(np.nan if msg.ttc_s is None else msg.ttc_s)
        out["raw"].append(msg.ttc_s is not None and msg.ttc_s < T)
        out["flag"].append(msg.approaching)
    return {k: np.array(v) for k, v in out.items()}


def transitions(flag) -> int:
    """How many times the flag changed state."""
    flag = np.asarray(flag, bool)
    return int(np.count_nonzero(flag[1:] != flag[:-1]))
