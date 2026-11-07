# Experiments tracker

> ⏳ **Everything on this page is WAITING FOR HARDWARE.** The code and analysis are built
> and tested on synthetic recordings; the "Synthetic dry run" column shows what each
> command prints on fake data, so you know what a sane answer looks like. Fill in the
> "Result" column from real recordings. Recordings go in `data/raw/` (git-ignored:
> recordings are never committed) and get copied to the team Drive.

Every recording starts with **5 s standing still** (the bias window). Press **Enter**
during `record.py` to log a MARK (e.g. "I'm facing the tape line now").

```bash
python scripts/record.py --out data/raw/<name>.csv            # Ctrl+C to stop
python scripts/analyze.py <what> data/raw/<name>.csv [--plot out/<name>.png]
```
Try any row now with fake data: `python scripts/make_fake_data.py`, then use `data/fake/<scene>.csv`.

## Page 4 · Axis, sign, tilt

| # | Do | Command | Synthetic dry run | Result |
|---|---|---|---|---|
| 4a | Worn as under the hardhat: 3× left 90°, 3× right 90°, 3× nod, 3× ear-to-shoulder. **First move must be a left turn**, and **don't skip the nods/tilts**: they reveal a mirrored gyro and the nose axis. **Nods before tilts.** | `analyze.py axis data/raw/axis.csv --plot out/axis.png --save` | axis gy, axis_sign −1, sign +1, gyro_sign +1 (handedness check 2.1° vs 8.8°), first turn 89.9°, nose_axis ±gz (nods vs tilts agree to 0.0°) | |
| 4c | Level: turn 90° left on the tape, MARK on arrival, back | `analyze.py tilt data/raw/tilt_level.csv` | axis-only 90.0, fix 90.0 | |
| 4c | Looking down ~40° | `analyze.py tilt data/raw/tilt_down.csv` | axis-only ~69, fix 90.0 | |
| 4c | Looking up ~20° | `analyze.py tilt data/raw/tilt_up.csv` | axis-only ~85, fix 90.0 | |
| 4c | Head tilted sideways ~20° | `analyze.py tilt data/raw/tilt_side.csv` | axis-only ~85, fix 90.0 | |

Run 4a with `--save` first: it writes the axis, signs and nose axis into
`calibration.json`. The 4c axis-only column means nothing until then, and yaw uses the
nose's heading (exact for look-down-and-tilt combinations) only once the nose axis is saved.
If `nose_check_deg` is over ~15°, the nods or tilts were sloppy: redo the recording.

## Page 5 · Drift

| # | Do | Command | Target | Synthetic dry run | Result |
|---|---|---|---|---|---|
| D1 | Muse on the desk, 10 min | `analyze.py drift data/raw/d1_desk.csv --plot out/d1.png` | < 1 °/min | 0.00 °/min | |
| D2 | Worn, sit still 5 min | `analyze.py drift data/raw/d2_worn.csv --save` | < 2 °/min | 0.18 °/min (15 without auto re-bias); still noise 0.15 °/s → re-bias threshold 0.3 | |
| D3 | 2 min looking around; every 30 s face the tape mark (laser on brim) + MARK | `analyze.py drift-marks data/raw/d3_active.csv` | < 10° after 2 min | final 0.1° | |
| D4 | D1 right after power-on, then again after 10 min on | `analyze.py drift` on both | know if we must warm up | | |
| D5 | 5 min walking, bending, carrying a box; MARK facing the start line now and then | `analyze.py drift-marks data/raw/d5_walk.csv` | note it | | |

`drift` prints the slope with and without auto re-bias. If they differ a lot, the bias
wanders once warm (guide's "D1 good, D2 bad" case) and auto re-bias is earning its keep.
It also prints the still noise; `--save` on the **D2** recording sets the auto re-bias
threshold to 2× that. If the two slopes are identical on a worn recording, re-bias never
fired: check the threshold.

**One-sentence verdict for Thu (fill in):** gyro-only is / is not good enough for a
2-minute run because ______.

## Page 6 · Accuracy, fast turns, glances

| # | Do | Command | Synthetic dry run | Result |
|---|---|---|---|---|
| 6a | Tape every 45°. Visit 45, 90, 135, 180, −45, −90, −135, 0 in that order, MARK on arrival, hold 3 s. **Turn slowly**: clipping ruins the estimate (the script warns). Repeat for 3 people. | `analyze.py scale data/raw/tape_<name>.csv --save` | 3.3% error found: scale 0.969 | |
| 6b | 10 fastest turns to the 90° line and back, MARK at each arrival | `analyze.py fast data/raw/fast.csv --plot out/fast.png` | 20 flat tops at 245, ~34° lost per snap turn | |
| 6c | 3 people × 10 natural glances at a target and back | `analyze.py glances data/raw/glance_<name>.csv --target <bearing>` | durations include time spent turning through the ±30° cone | |
| 6d | Start-of-run reset: face +x, press Enter in `live_yaw.py` | manual | | |
| 6d | Reference glance: sign on the wall at a measured bearing; look at it, still, for 1.5 s | `live_yaw.py --reference-bearing <deg>` | snaps once per look; drift must already be < 10° to be caught | |

If glance durations are often under 0.3 s, Jordan's threshold is too strict; send the list.

## Page 7 · Hardhat and Bluetooth

| # | Do | Command | Report | Result |
|---|---|---|---|---|
| 7a | Wear Muse + hardhat 20 min while moving. Two hat types. Yaw facing start line before/after 10 min of bending | `live_yaw.py` | comfort, slip (deg), which hat | |
| B1 | Stream 2 min at 1, 3, 5, 8 m, facing toward and away. Drops come from the Muse's packet counter, so every lost packet counts, even single ones. | `analyze.py bluetooth data/raw/b1_<d>m_<dir>.csv` | drops/min, samples lost | |
| B2 | 5 min in a crowd of phones/laptops | `analyze.py bluetooth ...` | drops, disconnects | |
| B3 | Switch band off/on, or walk out of range and back | watch `live_yaw.py` (prints "reconnecting" / "reconnected") | seconds until data flows; did the automatic reconnect work? | |
| B4 | 30 min continuous | `analyze.py bluetooth ...` | disconnects, battery drop | |
| B5 | Repeat B1 with a USB dongle (BLED112: `record.py --board muse_s_bled --serial-port COMx`) | `analyze.py bluetooth ...` | better or worse? | |

Synthetic B dry run (2% dropped 3-sample packets): 51.9 Hz, 52 drops, 156 samples lost,
biggest gap 83 ms. With LSL timestamps all 52 were invisible: a single lost packet (58 ms)
stays under the guide's 100 ms gap rule. The packet counter sees every one.

## Page 9 · With Hanh

| # | Do | Command | Synthetic dry run | Result |
|---|---|---|---|---|
| 9a | One session, both on `local_clock()`: 5 s still, 10 slow turns, 10 fast, 2 min free. Either `record.py --mqtt HOST --camera-topic <TOPIC>` (once agreed with Hanh) or merge Hanh's `cam.csv` into ours (see below) | — | | |
| 9b | Camera delay | `analyze.py lag data/raw/joint.csv` | 0.11 s (true 0.10), corr 0.92 | |
| 9d | K sweep, 10 s hidden windows | `analyze.py blend data/raw/joint.csv` | K=0.05: 0.6° median visible, 2.5° after hidden (was ~9° before the blend learned the bias) | |

Merging Hanh's per-stream file into ours:
```python
from headmotion.sources import read_csv, read_columns_csv, write_csv
s = read_csv("data/raw/joint_muse.csv") + read_columns_csv("data/raw/cam.csv", "CAM")
write_csv("data/raw/joint.csv", s)
```
Then set `camera_lag_s` and `blend_k` (and check `blend_ki`) in `calibration.json` and
mark them measured.

Live camera input exists (`live_yaw.py --mqtt HOST --camera-topic <TOPIC>`) but is
**waiting on Hanh** to agree the topic and fields. It expects the repo convention:
`{"v": 1, "t_s": <local_clock()>, "yaw_deg": ..., "valid": true}`.
