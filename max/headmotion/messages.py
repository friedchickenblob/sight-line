"""Messages Max publishes (page 10), in the repo's message convention:
JSON over MQTT, topics under sightline/, snake_case fields with unit suffixes,
and "v": 1 in every payload. Field names follow the guide's proposal except
where the convention needs a suffix (t -> t_s, battery -> battery_pct).
Jordan finalises the contract; bump SCHEMA_VERSION when a field changes meaning.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import ClassVar, Literal, Optional

SCHEMA_VERSION = 1


class _Message:
    TOPIC: ClassVar[str]

    def as_dict(self) -> dict:
        return {"v": SCHEMA_VERSION, **asdict(self)}

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), allow_nan=False, separators=(",", ":"))


@dataclass(frozen=True)
class HeadYaw(_Message):
    """~50 Hz, one per gyro sample."""
    TOPIC: ClassVar[str] = "sightline/head_yaw"
    t_s: float
    yaw_deg: Optional[float]          # None until the run is zeroed (reset or first camera frame)
    rate_dps: float                   # turn rate about true vertical, bias removed, tilt fixed
    source: Literal["gyro", "blend"]  # "blend" if a camera correction landed recently
    cam_age_s: Optional[float]        # seconds since the last accepted camera correction
    valid: bool                       # safe to use for the alert rule


@dataclass(frozen=True)
class Contact(_Message):
    """Every camera frame."""
    TOPIC: ClassVar[str] = "sightline/contact"
    t_s: float
    d_m: float
    ttc_s: Optional[float]
    d_min_m: float
    bearing_deg: float
    approaching: bool                 # after hysteresis


@dataclass(frozen=True)
class MuseHealth(_Message):
    """1 per second."""
    TOPIC: ClassVar[str] = "sightline/muse_health"
    t_s: float
    connected: bool
    rate_hz: float
    drops_per_min: float
    battery_pct: Optional[float] = None   # BrainFlow has no battery channel for the Muse S, so None there
