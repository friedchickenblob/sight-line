# BrainFlow data collection: architecture & guide

How the BrainFlow-based Muse S collection pipeline is organised, how head-direction tracking
works, and how to do the common tasks.

**How it relates to `muse_collection.py`:** that script records from muselsl's LSL streams and
computes gyro-only yaw. `brainflow_collection.py` connects to the headband directly through
BrainFlow (no separate muselsl process), fuses gyro and accelerometer into heading, pitch and roll,
and records EEG alongside. Both follow the repo conventions in `CLAUDE.md` (angles, time).

- [Overview](#overview)
- [Architecture](#architecture)
- [How it works](#how-it-works)
- [Output files](#output-files)
- [How-to](#how-to)
- [Troubleshooting](#troubleshooting)
- [Extending the code](#extending-the-code)

---

## Overview

SightLine streams a Muse S headband over BrainFlow, works out which way the wearer's head is facing
from the headband's motion sensor (IMU), and records each session to disk.

**Key limitation:** the Muse S IMU has an accelerometer and a gyroscope but **no magnetometer**.
The same is true of the Muse 2 and Muse S Athena in BrainFlow 5.23. Heading is therefore measured
relative to a reference the user sets, not as a compass bearing.

| Quantity | Source | Absolute? | Drift |
|---|---|---|---|
| Pitch (look up/down) | accel + gyro | Yes, via gravity | None |
| Roll (head tilt) | accel + gyro | Yes, via gravity | None |
| Heading (left/right) | integrated gyro | No, relative to the last tare (`z`) | Slow, roughly 0.1–1°/min after calibration (an estimate, not yet measured on hardware) |

**Status:** the pipeline runs end to end on BrainFlow's synthetic board and the 5 unit tests pass.
It has not yet been run on a real Muse S, so do the one-time axis check in [How-to](#first-time-axis-check-once-per-headband).

---

## Architecture

```
sight-line/
├── brainflow_collection.py  CLI entry point: connect → calibrate → live loop → save
├── muse_collection.py       (existing muselsl-based recorder, unchanged)
├── sightline/
│   ├── clock.py             pylsl.local_clock() time + BrainFlow Unix-time conversion
│   ├── device.py            BrainFlow connection, channel layouts, markers
│   ├── orientation.py       calibration, Madgwick filter, heading/pitch/roll, tare
│   └── recorder.py          session folder + CSV/JSON writers
├── tests/
│   └── test_orientation.py  simulated-motion tests for orientation.py
├── requirements-brainflow.txt  brainflow, numpy, pylsl, pytest
└── docs/
    └── brainflow-collection.md  this file
```

### Data flow

```
 Muse S ──BLE──▶ BrainFlow (BoardShim, ring buffer)
                   │
                   ├─ AUXILIARY_PRESET (52 Hz accel+gyro) ──▶ MuseDevice.pull_imu()
                   │                                              │
                   │                    ┌─────────────────────────┘
                   │                    ▼
                   │        calibrate_still()  (first 5 s, head still)
                   │                    │  gyro bias, up vector
                   │                    ▼
                   │        HeadOrientationTracker.update()  (per sample)
                   │          bias removal → MadgwickIMU → heading / pitch / roll
                   │                    │
                   │                    ▼
                   │        SessionRecorder ──▶ imu.csv   + live console line
                   │
                   └─ DEFAULT_PRESET (256 Hz EEG) ──▶ MuseDevice.pull_eeg() ──▶ eeg.csv

 Keyboard (z, 1–9, m, q) ──▶ brainflow_collection.py ──┬─▶ tracker.tare()
                                          ├─▶ events.csv
                                          └─▶ BrainFlow marker channel (both presets)
```

### Module responsibilities

| Module | Main pieces | Responsibility |
|---|---|---|
| `brainflow_collection.py` | `main()`, `run_calibration()`, `start_key_reader()` | Parses CLI flags, runs calibration, and runs the loop: poll IMU and EEG every 50 ms, update the tracker, write rows, handle keys, print status. Cleans up on `q`/Ctrl+C. |
| `sightline/clock.py` | `now()`, `from_unix()` | Project clock (`pylsl.local_clock()`). Converts BrainFlow's Unix timestamps using the offset between the two clocks, measured at startup. |
| `sightline/device.py` | `MuseDevice`, `BOARDS`, `ImuLayout`, `EegLayout` | Wraps BrainFlow `BoardShim`. Maps board names to `BoardIds`, reads channel numbers and sample rates from `get_board_descr()`, starts and stops the stream, inserts markers. |
| `sightline/orientation.py` | `calibrate_still()`, `MadgwickIMU`, `HeadOrientationTracker` | All the maths: gyro-bias and up-vector calibration, sensor fusion, heading around gravity, tare, optional auto-bias. Has no BrainFlow dependency, so it can be tested on its own. |
| `sightline/recorder.py` | `SessionRecorder` | Creates `data/<timestamp>_<subject>/` and writes `imu.csv`, `eeg.csv`, `events.csv` and `meta.json`. |
| `tests/test_orientation.py` | 5 tests | Simulates head turns (including a tilted mount), stillness drift, auto-bias and pitch sign. |

---

## How it works

Every IMU sample goes through bias removal, a sensor-fusion filter, and a projection onto the
horizontal plane. The result is heading, pitch and roll.

### 1. BrainFlow streams

BrainFlow splits Muse data into *presets*. SightLine reads two of them with
`get_board_data(preset=...)`, which returns every row received since the last call.

| Preset | Content | Rate | Channels (Muse S) |
|---|---|---|---|
| `AUXILIARY_PRESET` | Accelerometer (g), gyroscope (deg/s) | 52 Hz | accel rows 1–3, gyro rows 4–6 |
| `DEFAULT_PRESET` | EEG (µV) | 256 Hz | TP9, AF7, AF8, TP10 |
| `ANCILLARY_PRESET` | PPG (not used yet) | 64 Hz | optical rows 1–3 |

Channel numbers are never hard-coded: `device.py` reads them from `BoardShim.get_board_descr()`,
so the same code works for the Muse 2, Athena and the synthetic board.

### 2. Still calibration

The wearer holds still and faces forward for 5 s. `calibrate_still()` works out:

- **Gyro bias:** the average gyro reading at rest. Any bias left in turns directly into heading drift.
- **Up vector:** the average accelerometer direction in sensor coordinates. This tells the code
  which way is up, however the band sits on the head.

If gyro noise is above 2 deg/s, or the accelerometer magnitude varies by more than 0.03 g, the
wearer moved and calibration retries (up to 3 times). `--no-strict-calib` accepts it anyway.

### 3. Sensor fusion (Madgwick filter)

`MadgwickIMU` is the standard 6-axis Madgwick filter. The gyro gives smooth, fast rotation, and
the accelerometer slowly pulls the estimate back toward true gravity, which removes tilt drift.
The gain `beta` (default 0.04, flag `--beta`) sets how strongly it trusts the accelerometer.
The filter starts already lined up with the calibration up vector, so there is no settling period.

### 4. Heading, pitch and roll

A forward axis (default sensor `+x`) is made horizontal at calibration, then rotated into the world
frame with the filter's quaternion.

- **Heading** is the angle of that forward vector around the gravity axis, minus the angle stored at
  the last tare. It is unwrapped internally, then shown in −180…+180°.
- **Pitch** is how far the forward vector points up or down. **Roll** is the same for the right-ear axis.

Because heading is a rotation about gravity, it doesn't depend on how the band is mounted.
The forward-axis choice only affects pitch and roll.

**Sign conventions:** heading follows the repo convention: degrees, counter-clockwise positive seen
from above (+ = turned left), wrapped to [-180, 180). Tare while facing the floor +x direction
(toward marker 1) so that 0° = +x. Pitch + = looking up, roll + = right ear down.

### 5. Tare and drift control

- **Tare (`z`):** saves the current heading as the new 0°. Do this whenever the wearer faces a known reference.
- **Auto-bias (`--auto-bias`, off by default):** after more than 1 s of stillness, the gyro bias is
  slowly re-estimated (20 s time constant). This cuts drift on long sessions, but it can absorb very
  slow, deliberate turns.
- **Still flag:** each sample records whether the head has been still for more than 1 s, which helps
  when analysing the data later.

### 6. Markers

Tare, target and marker keypresses are written to `events.csv` and also into BrainFlow's marker
channel on both streams, so IMU and EEG can be lined up offline.

| Key | Event | Marker value |
|---|---|---|
| `z` | tare | 1 |
| `m` | marker | 2 |
| `1`–`9` | target N | 11–19 |

---

## Output files

Each run creates `data/<YYYYMMDD_HHMMSS>[_<subject>]/` (`data/` is gitignored; recordings go on the
shared drive). Every file has two time columns: `t` = `pylsl.local_clock()` seconds (the project
clock, same as the camera) and `timestamp_unix` = BrainFlow's original Unix time.

**`imu.csv`**: one row per IMU sample (52 Hz).

| Column | Meaning |
|---|---|
| `t` | project clock, `pylsl.local_clock()` (s) |
| `timestamp_unix` | BrainFlow timestamp, Unix (s) |
| `package` | BrainFlow package counter (gaps = dropped packets) |
| `ax_g`, `ay_g`, `az_g` | raw accelerometer (g) |
| `gx_dps`, `gy_dps`, `gz_dps` | raw gyroscope (deg/s), before bias removal |
| `heading_deg` | heading relative to last tare, CCW + (left) |
| `pitch_deg` | + = looking up |
| `roll_deg` | + = right ear down |
| `still` | 1 if the head has been still for > 1 s |
| `qw`, `qx`, `qy`, `qz` | full orientation quaternion (sensor → world) |

**`eeg.csv`**: `t, timestamp_unix, TP9, AF7, AF8, TP10` (µV, 256 Hz). Skipped with `--no-eeg`.
AF7/AF8 also pick up horizontal eye movements (EOG), which could help if you later want gaze
direction and not just head direction.

**`events.csv`**: `t, timestamp_unix, event, marker_value, label`. Events: `calibrated`, `tare`, `target`
(label `target_N`), `marker`, `stop`.

**`meta.json`**: board and ID, sample rates, EEG channel names, calibration values (gyro bias,
up vector, noise levels), the clock used, and the settings used (`forward_axis`, `invert_heading`, `auto_bias`, `beta`).

---

## How-to

### Set up (once)

```powershell
cd sight-line
python -m venv .venv
.venv\Scripts\pip install -r requirements-brainflow.txt
```

### Record a session

1. Turn the Muse S on. **Don't pair it in Windows Bluetooth settings**, and close the Muse app.
   BrainFlow connects to it directly over Bluetooth.
2. Run:
   ```powershell
   .venv\Scripts\python brainflow_collection.py --subject p01
   ```
   Add `--serial-number MuseS-1A2B` if more than one headband is nearby.
   For a BLED112 USB dongle, use `--board muse-s-bled --serial-port COM3`.
3. Keep the head still and facing forward for the 5 s calibration.
4. Watch the live line (`heading … pitch … roll …`) and use the keys:
   `z` to zero heading, `1`–`9` to mark targets, `m` for a marker, `q` to quit.
5. The session folder path is printed when it stops.

Useful flags: `--duration 300` (stop after 5 min), `--calib-seconds 8`, `--auto-bias`,
`--no-eeg`, `--out D:\sightline-data`, `--debug` (BrainFlow logging).
`python brainflow_collection.py --help` lists them all.

### First-time axis check (once per headband)

Muse axis directions aren't documented consistently, so check them once:

1. Turn your head **left**. Heading should go **positive** (CCW). If it goes negative, always run with `--invert-heading`.
2. Look **up**. Pitch should go **positive**. If pitch or roll respond to the wrong movement, try
   `--forward-axis -x`, `+y` or `-y` until they're right. Heading isn't affected by this flag.
3. Write down the flags that worked for your headband.

### Collect ground truth / measure accuracy

1. Put targets around the room at known angles from a "front" point, e.g. 0°, ±45°, ±90°.
2. Face the front target (floor +x) and press `z`.
3. Turn to target N and press its number key (`1`–`9`). Repeat in any order.
4. Afterwards, compare `heading_deg` in `imu.csv` at each `target` time (`t`) in `events.csv` with
   the known angle. Comparing the front target over time gives you the drift rate.

### Dry run without hardware

```powershell
.venv\Scripts\python brainflow_collection.py --board synthetic --duration 10
```

This uses BrainFlow's synthetic board (random signals), so the angles are meaningless,
but it checks the whole pipeline and the files it writes.

### Run the tests

```powershell
.venv\Scripts\python -m pytest
```

### Load a session for analysis

```python
import pandas as pd
from pathlib import Path

s = Path("data/20261106_193631_p01")
imu = pd.read_csv(s / "imu.csv")
events = pd.read_csv(s / "events.csv")

# heading at each ground-truth target press
targets = events[events.event == "target"]
idx = imu.t.searchsorted(targets.t)
print(pd.DataFrame({"target": targets.label.values,
                    "heading_deg": imu.heading_deg.iloc[idx.clip(max=len(imu) - 1)].values}))
```

(`pandas` isn't in `requirements-brainflow.txt`. Install it with `.venv\Scripts\pip install pandas`.)

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Connection times out / board not found | Headband off, already connected to the Muse app or phone, or paired in Windows. Unpair it, close other apps, try `--timeout 30`, and add `--serial-number`. |
| "only N IMU samples arrived" | The stream isn't flowing. Reconnect, check the battery, and run with `--debug`. |
| Calibration keeps failing with "movement detected" | Head moved, or the band is loose. Rest your head against something, use `--calib-seconds 8`, or as a last resort `--no-strict-calib`. |
| Heading goes the wrong way | Use `--invert-heading`. |
| Pitch/roll react to the wrong movement | Change `--forward-axis` (see the axis check). |
| Heading slowly creeps while still | Normal gyro drift. Re-tare with `z`, recalibrate (restart), or try `--auto-bias`. |
| Heading lags or overshoots in fast turns | Unusual. Check for gaps in `package` (dropped Bluetooth packets). |
| `ValueError: forward axis ... is (nearly) vertical` | The chosen forward axis points up when worn. Pick a different one. |

---

## Extending the code

- **New board:** add an entry to `BOARDS` in `sightline/device.py`. Layouts come from BrainFlow automatically.
- **Use orientation elsewhere:** `HeadOrientationTracker` only needs accel (g) and gyro (deg/s) arrays,
  so it can be used without BrainFlow (e.g. on recorded `imu.csv` data, or in a live app).
- **Stream heading to another program:** in `brainflow_collection.py`, send `sample.heading_deg` etc. right after
  `tracker.update()` (e.g. UDP or LSL).
- **PPG:** read `ANCILLARY_PRESET` the same way `pull_eeg()` does. On some Muse models it has to be
  enabled first with `config_board()`.
- **Absolute heading:** this needs an outside reference, since the Muse has no magnetometer. Options are
  a phone or second IMU with a magnetometer, a camera, or regular tares at known targets.
- **Tests:** add cases to `tests/test_orientation.py`. The `_turn()` helper simulates rotation about vertical.
