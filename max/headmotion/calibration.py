"""Calibration constants and whether each one has been measured yet.

Every value starts as a PLACEHOLDER taken from the research guide. Replace it
with the measured number (and flip its status to "measured") once the matching
experiment has been run on our Muse. `pending()` lists what is still unmeasured
so the demo can warn instead of silently trusting a guess.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "calibration.json"


@dataclass
class Calibration:
    gyro_sign: float = 1.0            # -1 if the gyro is mirrored relative to the accel (fit on nods, page 4a)
    sign: float = 1.0                 # +1 if a left turn reads positive on the tilt-fixed rate (page 4a)
    yaw_axis: int = 2                 # which raw gyro axis spikes for turns, 0/1/2 = gx/gy/gz (page 4a)
    axis_sign: float = 1.0            # sign of that raw axis for a left turn (page 4a)
    scale: float = 1.0                # multiply rate by this (page 6a)
    gyro_limit_dps: float = 245.0     # clipping level (page 6b). 245 = muselsl's 0.0074768 deg/s per LSB x 32767
    gravity_alpha: float = 0.02       # accel low-pass weight (page 4b; try 0.01-0.05)
    camera_lag_s: float = 0.10        # camera delay, from cross-correlation (page 9b)
    blend_k: float = 0.05             # pull per camera frame (page 9d)
    blend_ki: float = 0.003           # bias learned per camera frame, deg/s per deg of error (page 9d)
    nose_axis: Optional[list] = None  # forward axis in gyro axes (page 4a nods + tilts); None = integrate about vertical
    rebias_max_std_dps: float = 0.6   # auto re-bias only when the gyro is this quiet (page 5, D2)
    status: dict = field(default_factory=lambda: {
        "gyro_sign": "PLACEHOLDER - needs axis experiment nods (page 4a)",
        "sign": "PLACEHOLDER - needs axis experiment (page 4a)",
        "yaw_axis": "PLACEHOLDER - needs axis experiment (page 4a)",
        "axis_sign": "PLACEHOLDER - needs axis experiment (page 4a)",
        "scale": "PLACEHOLDER - needs tape-line accuracy test (page 6a)",
        "gyro_limit_dps": "PLACEHOLDER - from muselsl scale factor; confirm flat tops in fast-turn test (page 6b)",
        "gravity_alpha": "PLACEHOLDER - tune on a nod recording (page 4b)",
        "camera_lag_s": "PLACEHOLDER - needs joint recording with Hanh (page 9b)",
        "blend_k": "PLACEHOLDER - needs joint recording with Hanh (page 9d)",
        "blend_ki": "PLACEHOLDER - tuned on synthetic bias creep; check on the joint recording (page 9d)",
        "nose_axis": "PLACEHOLDER - needs axis experiment nods + tilts (page 4a)",
        "rebias_max_std_dps": "PLACEHOLDER - set from D2 worn-still noise: analyze.py drift --save (page 5)",
    })

    def pending(self) -> list[str]:
        return [k for k, v in self.status.items() if not str(v).startswith("measured")]

    def save(self, path=DEFAULT_PATH):
        Path(path).write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, path=DEFAULT_PATH) -> "Calibration":
        path = Path(path)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data and k != "status"}
        cal = cls(**known)
        cal.status.update(data.get("status", {}))   # fields added later stay PLACEHOLDER
        return cal
