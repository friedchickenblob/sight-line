"""Project clock: pylsl.local_clock() seconds (see CLAUDE.md "Time").

BrainFlow stamps samples with Unix time; we convert them to the LSL clock using the
offset between the two clocks, measured once at import, so Muse data lines up
with the camera frames recorded on the same laptop.
"""

from __future__ import annotations

import time

try:
    from pylsl import local_clock
    CLOCK_NAME = "pylsl.local_clock"
except ImportError:  # still records, but timestamps won't share the camera's clock
    local_clock = None
    CLOCK_NAME = "unix (pylsl not installed)"

_UNIX_MINUS_LSL = time.time() - local_clock() if local_clock else 0.0


def now() -> float:
    """Current time on the project clock."""
    return local_clock() if local_clock else time.time()


def from_unix(t_unix: float) -> float:
    """Convert a BrainFlow (Unix) timestamp to the project clock."""
    return t_unix - _UNIX_MINUS_LSL
