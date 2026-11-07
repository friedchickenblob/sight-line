"""Session recording: one folder per session with CSVs + metadata."""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path

from sightline import clock

IMU_COLUMNS = ["t", "timestamp_unix", "package", "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps",
               "heading_deg", "pitch_deg", "roll_deg", "still", "qw", "qx", "qy", "qz"]


class SessionRecorder:
    def __init__(self, root: str | Path, subject: str = "", eeg_names: list[str] | None = None):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{stamp}_{subject}" if subject else stamp
        self.dir = Path(root) / name
        self.dir.mkdir(parents=True, exist_ok=False)

        self._imu_f = open(self.dir / "imu.csv", "w", newline="")
        self.imu = csv.writer(self._imu_f)
        self.imu.writerow(IMU_COLUMNS)

        self._events_f = open(self.dir / "events.csv", "w", newline="")
        self.events = csv.writer(self._events_f)
        self.events.writerow(["t", "timestamp_unix", "event", "marker_value", "label"])

        self._eeg_f = None
        if eeg_names is not None:
            self._eeg_f = open(self.dir / "eeg.csv", "w", newline="")
            self.eeg = csv.writer(self._eeg_f)
            self.eeg.writerow(["t", "timestamp_unix", *eeg_names])

    def write_meta(self, meta: dict) -> None:
        (self.dir / "meta.json").write_text(json.dumps(meta, indent=2, default=_jsonable))

    def event(self, event: str, label: str = "", marker_value: float | str = "") -> None:
        """Log an event now; `t` is on the project clock (pylsl.local_clock)."""
        self.events.writerow([f"{clock.now():.6f}", f"{time.time():.6f}", event, marker_value, label])
        self._events_f.flush()

    def flush(self) -> None:
        for f in (self._imu_f, self._eeg_f, self._events_f):
            if f:
                f.flush()

    def close(self) -> None:
        for f in (self._imu_f, self._eeg_f, self._events_f):
            if f:
                f.close()


def _jsonable(o):
    try:
        return o.tolist()
    except AttributeError:
        return str(o)
