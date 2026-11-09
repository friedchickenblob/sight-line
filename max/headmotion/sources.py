"""One way to read motion data, whether it comes from the Muse, a file or the
simulator (page 7c, the replay safety net). Nothing downstream can tell which.

Data shape: a stream of Sample(kind, t, v) in time order, delivered in batches.
  kind  "GYRO" (deg/s, 3 axes) | "ACC" (g, 3 axes) | "CAM" (yaw_deg,) | "MARK" (index,)
        | "RESET" (yaw_deg,)  operator re-zero, recorded so replay does the same
        | "DROP" (samples_lost,)  packets the headband sent that never arrived
  t     seconds on pylsl.local_clock() (or the recording's clock on replay)
A batch may be empty: that is how a consumer notices the stream went quiet.
"""
from __future__ import annotations

import csv
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Protocol, Sequence

GYRO, ACC, CAM, MARK, RESET, DROP = "GYRO", "ACC", "CAM", "MARK", "RESET", "DROP"
KINDS = (GYRO, ACC, CAM, MARK, RESET, DROP)


@dataclass(frozen=True)
class Sample:
    kind: str
    t: float
    v: tuple

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown sample kind {self.kind!r}")


class Source(Protocol):
    def batches(self) -> Iterator[list[Sample]]: ...
    def now(self) -> float: ...


class Extra(Protocol):
    """Anything else that produces samples while the Muse streams (e.g. Hanh's camera over MQTT)."""
    def drain(self) -> list[Sample]: ...


# --------------------------------------------------------------------- live
class Dejitter:
    """Regular timestamps from a sample counter.

    BrainFlow stamps each Muse packet with the time it arrived and spreads its
    3 samples between the previous arrival and this one. Bluetooth delivers in
    bursts, so that timing jitters, and a lost packet gets smeared over the next
    one. The headband itself samples at a steady rate, so fit
    arrival = t0 + n * period by least squares (old samples fade out with
    `halflife_s`) and stamp sample n with the fitted line instead. This is what
    LSL's dejitter did for muselsl; here the counter includes lost samples, so
    smoothing can't hide a drop.
    """

    def __init__(self, nominal_hz: float, halflife_s: float = 30.0, warmup: int = 20):
        self.period = 1.0 / nominal_hz
        self.decay = 0.5 ** (1.0 / (halflife_s * nominal_hz))
        self.warmup = warmup
        self.reset()

    def reset(self):
        self._n0 = self._t0 = None
        self._w = self._sx = self._sy = self._sxx = self._sxy = 0.0
        self._count = 0
        self._last: Optional[float] = None

    def stamp(self, n: int, t_arrival: float) -> float:
        if self._n0 is None:
            self._n0, self._t0 = n, t_arrival
        x, y = float(n - self._n0), t_arrival - self._t0
        d = self.decay
        self._w = d * self._w + 1.0
        self._sx = d * self._sx + x
        self._sy = d * self._sy + y
        self._sxx = d * self._sxx + x * x
        self._sxy = d * self._sxy + x * y
        self._count += 1
        out = t_arrival
        den = self._w * self._sxx - self._sx ** 2
        if self._count >= self.warmup and den > 1e-9:
            b = (self._w * self._sxy - self._sx * self._sy) / den
            if 0.9 * self.period < b < 1.1 * self.period:      # otherwise the fit is not trustworthy yet
                out = self._t0 + (self._sy - b * self._sx) / self._w + b * x
        if self._last is not None:
            out = max(out, self._last + 0.5 * self.period)       # never step backwards
        self._last = out
        return out


class PacketDecoder:
    """BrainFlow rows -> Samples, counting lost packets from the headband's packet counter.

    The Muse numbers every Bluetooth packet (16-bit counter, 3 gyro/accel samples
    each); BrainFlow copies the number into every row. A jump of k in the counter
    means k - 1 packets never arrived. That is exact, unlike the guide's
    "gap over 100 ms" rule, which a single lost packet (58 ms) never trips.
    """

    def __init__(self, nominal_hz: float, modulus: int = 65536, samples_per_packet: int = 3,
                 max_lost_packets: int = 100):
        self.hz, self.modulus, self.spp, self.max_lost = nominal_hz, modulus, samples_per_packet, max_lost_packets
        self.dejitter = Dejitter(nominal_hz)
        self.lost_samples = 0
        self._pkg: Optional[int] = None
        self._group = 0
        self._n = 0
        self._last_arrival: Optional[float] = None

    def reset(self):
        """After a reconnect the counter and the clock fit both start over."""
        self._pkg = None
        self.dejitter.reset()

    def decode(self, pkg, t_arrival, acc, gyro) -> list[Sample]:
        """Arrays of one value (pkg, t_arrival) or 3-vector (acc, gyro) per row, in arrival order."""
        out = []
        for p, ta, a, g in zip(pkg, t_arrival, acc, gyro):
            p, ta = int(p), float(ta)
            lost = 0
            if self._pkg is None:
                restart = self._last_arrival is not None
                if restart:          # reconnect: estimate what we missed from the clock
                    lost = max(int(round((ta - self._last_arrival) * self.hz)) - 1, 0)
                self._n, self._group = 0, 1
            elif p == self._pkg:
                self._n += 1
                self._group += 1
            else:
                jump = (p - self._pkg) % self.modulus
                self.spp = self._group or self.spp       # learn samples per packet from the last full one
                self._group = 1
                if jump <= self.max_lost:
                    lost = (jump - 1) * self.spp
                    self._n += 1 + lost
                else:                # counter reset or a long outage: start the clock fit over
                    lost = max(int(round((ta - self._last_arrival) * self.hz)) - 1, 0)
                    self.dejitter.reset()
                    self._n = 0
            self._pkg = p
            self._last_arrival = ta
            t = self.dejitter.stamp(self._n, ta)
            if lost:
                self.lost_samples += lost
                out.append(Sample(DROP, t, (float(lost),)))
            out.append(Sample(ACC, t, tuple(float(x) for x in a)))
            out.append(Sample(GYRO, t, tuple(float(x) for x in g)))
        return out


# board name -> (BoardIds member, packet counter modulus, samples per packet)
BOARDS = {
    "muse_s": ("MUSE_S_BOARD", 65536, 3),            # Muse S Gen 2, laptop's own Bluetooth
    "muse_s_bled": ("MUSE_S_BLED_BOARD", 65536, 3),  # Muse S Gen 2 through a BLED112 USB dongle (B5)
    "synthetic": ("SYNTHETIC_BOARD", 256, 1),        # BrainFlow's fake board: exercises this code without a Muse
}


class LiveMuse:
    """Muse S (Gen 2) over BrainFlow: no muselsl, BlueMuse or LSL streams needed.

    Gyro + accelerometer come from BrainFlow's AUXILIARY preset (52 Hz, deg/s and g,
    same scale factors muselsl used). Timestamps are moved onto this laptop's
    pylsl.local_clock(), so they line up with Hanh's camera frames, and
    de-jittered from the packet counter (see Dejitter).

    If no data arrives for `reconnect_after_s`, the session is torn down and
    prepared again (B3: no manual restart needed). Human-readable notes about
    that go to `events`.

    connect=False starts without the band: nothing is read and nothing retries
    until search() finds it (the dashboard's "Search for Muse" button). From then
    on a lost connection reconnects by itself as above.
    """

    def __init__(self, board: str = "muse_s", name: str | None = None, mac: str | None = None,
                 serial_port: str | None = None, muse_preset: str | None = None, timeout_s: float = 15.0,
                 poll_s: float = 0.01, extra: Sequence[Extra] = (), reconnect_after_s: float = 3.0,
                 clock: Callable[[], float] | None = None, connect: bool = True):
        """name: the band's Bluetooth name (e.g. MuseS-1A2B) when several are around; mac: its address.
        serial_port: COM port of the BLED112 dongle (board="muse_s_bled" only).
        muse_preset: Muse preset string for BrainFlow's other_info; None = BrainFlow's default (p21)."""
        from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams

        if board not in BOARDS:
            raise ValueError(f"unknown board {board!r}: use one of {', '.join(BOARDS)}")
        if clock is None:
            from pylsl import local_clock as clock
        self._clock = clock
        self._poll_s, self._extra, self._reconnect_after = poll_s, list(extra), reconnect_after_s
        member, modulus, spp = BOARDS[board]
        self.board_id = int(getattr(BoardIds, member))
        self._params = BrainFlowInputParams()
        self._params.timeout = int(timeout_s)
        if name:
            self._params.serial_number = name
        if mac:
            self._params.mac_address = mac
        if serial_port:
            self._params.serial_port = serial_port
        if muse_preset:
            self._params.other_info = muse_preset
        self._preset = self._motion_preset(BoardShim, self.board_id)
        d = BoardShim.get_board_descr(self.board_id, self._preset)
        self._rows = (d["package_num_channel"], d["timestamp_channel"], d["accel_channels"], d["gyro_channels"])
        self._battery_row = d.get("battery_channel")        # the Muse S has none in BrainFlow
        self.battery_pct: Optional[float] = None
        self.decoder = PacketDecoder(float(d["sampling_rate"]), modulus, spp)
        self.events: deque[str] = deque(maxlen=50)
        BoardShim.disable_board_logger()
        self._BoardShim = BoardShim
        self._board = None
        self._last_data = self._clock()
        self._auto_reconnect = connect           # off until the first connection when connect=False
        self._link_lock = threading.Lock()       # one connect attempt at a time (search vs. reconnect)
        self.searching = False
        if connect:
            self._connect()

    @property
    def connected(self) -> bool:
        return self._board is not None

    def search(self) -> bool:
        """Try once to find and open the band. Blocks up to timeout_s, so call it from a
        background thread; poll() keeps running meanwhile. Returns whether it is connected."""
        if not self._link_lock.acquire(blocking=False):
            return self.connected                # a search or reconnect is already running
        try:
            if self._board is not None:
                return True
            self.searching = True
            self.events.append("searching for the Muse")
            self.decoder.reset()                 # before _connect: poll() starts decoding once _board is set
            try:
                self._connect()
            except RuntimeError as e:
                self.events.append(str(e))
                return False
            self._auto_reconnect = True
            self.events.append("Muse connected")
            return True
        finally:
            self.searching = False
            self._link_lock.release()

    @staticmethod
    def _motion_preset(BoardShim, board_id):
        for preset in BoardShim.get_board_presets(board_id):
            if "gyro_channels" in BoardShim.get_board_descr(board_id, preset):
                return preset
        raise RuntimeError(f"board {board_id} has no gyro channels")

    def _connect(self):
        from brainflow.board_shim import BrainFlowError
        board = self._BoardShim(self.board_id, self._params)
        try:
            board.prepare_session()
            board.start_stream()
        except BrainFlowError as e:
            try:
                board.release_session()
            except BrainFlowError:
                pass
            raise RuntimeError(f"could not start the Muse over BrainFlow ({e}). Is it switched on, "
                               "not connected to the Muse app or another program, and in range?") from e
        self._board = board
        self._last_data = self._clock()

    def _disconnect(self):
        from brainflow.board_shim import BrainFlowError
        if self._board is None:
            return
        for step in (self._board.stop_stream, self._board.release_session):
            try:
                step()
            except BrainFlowError:
                pass
        self._board = None

    def close(self):
        self._disconnect()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def now(self) -> float:
        return self._clock()

    def _maybe_reconnect(self, now: float):
        if not self._auto_reconnect or now - self._last_data < self._reconnect_after:
            return
        if not self._link_lock.acquire(blocking=False):
            return                               # search() is connecting right now
        try:
            self._reconnect(now)
        finally:
            self._link_lock.release()

    def _reconnect(self, now: float):
        self.events.append(f"no Muse data for {now - self._last_data:.1f} s: reconnecting")
        self._disconnect()
        try:
            self._connect()
            self.decoder.reset()
            self.events.append("reconnected")
        except RuntimeError as e:
            self.events.append(str(e))
            self._last_data = self._clock()          # wait another reconnect_after_s before retrying

    def poll(self) -> list[Sample]:
        """One non-blocking read: everything that arrived since the last call."""
        batch: list[Sample] = []
        now = self._clock()
        board = self._board
        data = None
        if board is not None:
            try:
                data = board.get_board_data(preset=self._preset)
            except Exception as e:                  # BrainFlowError: session gone (e.g. half-made reconnect)
                if self._link_lock.acquire(blocking=False):
                    try:
                        if self._board is board:
                            self.events.append(f"Muse read failed ({e}): dropping the session, will reconnect")
                            self._disconnect()
                    finally:
                        self._link_lock.release()
            if data is not None and data.shape[1]:
                offset = now - time.time()                 # BrainFlow stamps with wall-clock time
                pkg, ts, acc, gyro = self._rows
                batch = self.decoder.decode(data[pkg], data[ts] + offset, data[acc].T, data[gyro].T)
                if self._battery_row is not None:
                    self.battery_pct = float(data[self._battery_row, -1])
                self._last_data = now
        self._maybe_reconnect(now)
        for x in self._extra:
            batch += x.drain()
        batch.sort(key=lambda s: s.t)
        return batch

    def batches(self) -> Iterator[list[Sample]]:
        while True:
            yield self.poll()
            time.sleep(self._poll_s)


# ------------------------------------------------------------------- replay
class Replay:
    """Plays recorded (or synthetic) samples back through the same interface.

    realtime=False runs as fast as possible (offline tuning, tests).
    realtime=True sleeps so the demo pipeline sees the original pacing.
    """

    def __init__(self, samples: Iterable[Sample], realtime: bool = False, batch_s: float = 0.02, speed: float = 1.0):
        self._samples = sorted(samples, key=lambda s: s.t)
        self._realtime = realtime
        self._batch_s = batch_s
        self._speed = speed
        self._now = self._samples[0].t if self._samples else 0.0

    def now(self) -> float:
        return self._now

    def batches(self) -> Iterator[list[Sample]]:
        if not self._samples:
            return
        i, n = 0, len(self._samples)
        t = self._samples[0].t
        wall0 = time.monotonic()
        t0 = t
        while i < n:
            t += self._batch_s
            batch = []
            while i < n and self._samples[i].t < t:
                batch.append(self._samples[i])
                i += 1
            self._now = t
            if self._realtime:
                delay = (t - t0) / self._speed - (time.monotonic() - wall0)
                if delay > 0:
                    time.sleep(delay)
            yield batch

    @classmethod
    def from_csv(cls, path, **kw) -> "Replay":
        return cls(read_csv(path), **kw)


# ---------------------------------------------------------------------- CSV
HEADER = ["kind", "t", "v0", "v1", "v2"]


def write_csv(path, samples: Iterable[Sample]) -> int:
    n = 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for s in samples:
            w.writerow([s.kind, repr(s.t), *[repr(float(x)) for x in s.v]])
            n += 1
    return n


def read_csv(path) -> list[Sample]:
    """Read our long-format recording (kind,t,v0,v1,v2). Empty cells are skipped."""
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            v = tuple(float(row[k]) for k in ("v0", "v1", "v2") if row.get(k) not in (None, ""))
            out.append(Sample(row["kind"], float(row["t"]), v))
    return out


def read_columns_csv(path, kind: str, t_col: str = "t") -> list[Sample]:
    """Read a per-stream file such as Hanh's cam.csv (t,yaw_deg) or gyro.csv (t,gx,gy,gz)."""
    out = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        cols = [c for c in reader.fieldnames if c != t_col]
        for row in reader:
            if any(row[c] in ("", None) for c in cols):
                continue
            out.append(Sample(kind, float(row[t_col]), tuple(float(row[c]) for c in cols)))
    return out


class CsvRecorder:
    """Write every sample that passes through, so every session can be replayed.

        with CsvRecorder("session.csv") as rec:
            for batch in rec.tee(source.batches()):
                ...
    """

    def __init__(self, path):
        self.path = Path(path)
        self.count = 0

    def __enter__(self):
        self._f = open(self.path, "w", newline="")
        self._w = csv.writer(self._f)
        self._w.writerow(HEADER)
        return self

    def write(self, s: Sample):
        self._w.writerow([s.kind, repr(s.t), *[repr(float(x)) for x in s.v]])
        self.count += 1

    def tee(self, batches: Iterator[list[Sample]]) -> Iterator[list[Sample]]:
        for batch in batches:
            for s in batch:
                self.write(s)
            yield batch

    def __exit__(self, *exc):
        self._f.close()


def open_source(spec: str, realtime: bool = False, **muse_kw) -> Source:
    """'muse' | 'replay:<file.csv>' | 'synthetic:<scene>'  (see synth.SCENE_NAMES).
    muse_kw go to LiveMuse (board, name, mac, serial_port, extra, ...)."""
    kind, _, arg = spec.partition(":")
    if kind == "muse":
        return LiveMuse(**{k: v for k, v in muse_kw.items() if v is not None})
    if kind == "replay":
        return Replay.from_csv(arg, realtime=realtime)
    if kind == "synthetic":
        from . import synth
        return Replay(synth.scene(arg or "joint"), realtime=realtime)
    raise ValueError(f"unknown source {spec!r}: use muse, replay:<file>, or synthetic:<scene>")
