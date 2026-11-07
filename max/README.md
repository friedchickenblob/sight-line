# Max: head motion + contact maths

Muse S gyro → head yaw on the shared floor frame (~50 Hz), and the maths that decides
when a machine is really about to reach someone. Implements the *Max research guide*
(NatHacks 2026, Oct 30 → Nov 5).

> **Rules caveat (guide p.1):** until Jordan confirms the hackathon rules, treat this as
> prep-repo code. The measurements, maths and write-ups carry over.

## Quick start (Windows, from this `max/` folder)

First time on a Windows laptop? Follow [docs/windows-setup.md](docs/windows-setup.md):
step by step from installing Python to the live dashboard, plus common errors and fixes.

```bash
python -m venv .venv
.venv\Scripts\activate             # Git Bash: source .venv/Scripts/activate
pip install -r requirements.txt
python -m pytest                   # 120 tests, synthetic data + BrainFlow's synthetic board, ~1-2 min
python scripts/make_fake_data.py   # one fake recording per experiment -> data/fake/
python scripts/live_yaw.py --source synthetic:joint      # watch fake live yaw
python scripts/simulate_contact.py --plot out/contact.png
```

With the headband (Muse S Gen 2, see [docs/streaming-recipe.md](docs/streaming-recipe.md)).
BrainFlow opens it directly over Bluetooth: one terminal, nothing else to start.

```bash
python scripts/live_yaw.py --record data/raw/session_01.csv
python scripts/live_yaw.py --mqtt localhost          # also publish head_yaw + muse_health
```

### Live dashboard

```bash
python dashboard/app.py                          # Muse S; open http://127.0.0.1:5000
python dashboard/app.py --source synthetic:joint # no headband: fake session in real time
```

Every CSV field live (GYRO, ACC, CAM, MARK, RESET, DROP rows: `kind, t, v0, v1, v2`),
head yaw, stream health, gyro / accel / yaw charts, and buttons to **start and stop
recording** (to `data/raw/<name>.csv`, same format as `record.py`), **Mark** and
**Re-zero**. Flask + plain HTML/CSS/JS, streamed with Server-Sent Events. Takes the
same `--source`, `--board`, `--mqtt` options as the scripts; `--host 0.0.0.0` opens it
to other devices on the network.

Every script takes `--source muse | replay:<csv> | synthetic:<scene>`. Downstream code
cannot tell the three apart; that is the replay safety net (guide p.7c). `--board synthetic`
runs the live BrainFlow path against BrainFlow's fake board when there is no headband.
Recordings in `data/raw/` are git-ignored (CLAUDE.md: never commit recordings); keep them
on the team Drive.

## Status board

✅ = built and verified on synthetic data · ⏳ = **waiting for hardware**: code and
analysis are ready, the measurement is not. Commands are in
[docs/experiments.md](docs/experiments.md).

| Guide item | Status | Where |
|---|---|---|
| Stream gyro + accel into Python (p.3) | ✅ BrainFlow, run on its synthetic board · ⏳ cold-start test on our Muse | `sources.LiveMuse`, `scripts/record.py` |
| Identify headband model (p.3 step 0) | ✅ Muse S Gen 2 → BrainFlow `MUSE_S_BOARD` | [streaming-recipe.md](docs/streaming-recipe.md) |
| Axis + sign experiment (p.4a) | ✅ analysis (also detects a mirrored gyro, finds the nose axis) · ⏳ recording | `analyze.py axis` |
| Tilt fix (p.4b–c) | ✅ 90° turn reads 90.0° looking down 40° (axis-only: 69°); look-down-and-tilt returns to 0.0° (was ~13° per cycle) | `yaw.Orientation` |
| Bias, integration, gap skipping (p.5a–b) | ✅ | `yaw.HeadYawEstimator` |
| Drift D1–D5 (p.5c) | ✅ analysis · ⏳ **all five measurements** | `analyze.py drift`, `drift-marks` |
| Scale factor (p.6a) | ✅ recovers a 3.3% error to 0.1% · ⏳ 3 people | `analyze.py scale` |
| Fast turns / clipping (p.6b) | ✅ detection · ⏳ how often it really happens | `analyze.py fast` |
| Glance durations (p.6c) | ✅ analysis · ⏳ 3 people × 10 glances | `analyze.py glances` |
| Start-of-run + reference-glance reset (p.6d) | ✅ both in `live_yaw.py` (Enter; `--reference-bearing`) · ⏳ test both | `est.reset()`, `yaw.ReferenceGlance` |
| Hardhat fit (p.7a) | ⏳ with Sean | [experiments.md](docs/experiments.md) |
| Bluetooth B1–B5 (p.7b) | ✅ exact drop counter (packet numbers) + watchdog + auto-reconnect · ⏳ **all five tests** | `health.py`, `analyze.py bluetooth` |
| Replay a recorded session (p.7c) | ✅ bit-identical yaw from CSV replay | `sources.Replay`, `CsvRecorder` |
| `contact()` + hysteresis + 5 scenes (p.8) | ✅ all scenes behave over 10 noise seeds | `contact.py`, `contact_sim.py` |
| Run `contact()` on Zoe's clips C, D (p.8) | ⏳ waiting on Zoe's data | `ContactTracker` |
| Camera delay + blend with Hanh (p.9) | ✅ filter + K sweep; < 5° after 10 s hidden (2.5° on the joint scene) · ⏳ joint recording with Hanh | `analyze.py lag`, `blend` |
| Messages `head_yaw`, `contact`, `muse_health` (p.10) | ✅ JSON over MQTT, `"v": 1`, unit-suffixed fields · ⏳ Jordan finalises | `messages.py`, `bus.py` |
| Camera yaw from Hanh, live | ✅ MQTT subscriber · ⏳ Hanh to agree topic + fields | `bus.CameraYawSubscriber` |

## What's different from the guide's code, and why

Each of these came from a failing test, not from taste. The full trail is in [decisions.tsv](decisions.tsv).

1. **"Up" is rotated by the gyro, not only averaged from the accelerometer.** The guide's
   accelerometer-only average lags a nod by ~1 s, and turns made during the nod get
   mis-projected. On 2 min of synthetic free movement (with realistic head-on-neck
   acceleration) that gave 11° p95 yaw error; rotating "up" with the gyro gives 2.4°.
2. **Camera corrections also shift the stored gyro history.** Without that, the next
   few late camera frames are compared against stale history and pull for the same
   error again.
3. **After the gyro clips, the camera is trusted harder (K=0.2) for 1 s.** K=0.05 left 6°
   of a 27° clip error after 1 s, missing the p.9 target.
4. **Auto re-bias only accepts a bias close to the old one.** A slow, steady turn has low
   jitter too. Without this guard it would be swallowed into the bias.
5. **BrainFlow instead of muselsl/LSL** (the guide's p.3 default). The clip level is
   still 245 °/s (same 0.0074768 scale factor). BrainFlow stamps packets with wall-clock
   arrival time, so samples are moved onto `local_clock()` and de-jittered from the
   Muse's packet counter. That counter also makes drop counting exact: the guide's
   "gap over 100 ms" rule misses every single lost packet (58 ms).
6. **Yaw is the heading of the nose axis** once the axis experiment has found it
   (see [docs/head-yaw.md](docs/head-yaw.md)). Integrating the rate about vertical, as
   the guide does, drifts ~13° per look-down-and-tilt cycle; the heading can't.
7. **The camera blend also learns the gyro bias** (an integral term, `blend_ki`). With
   the bias creeping as the band warms, a blind spot late in a session drifted ~9° in
   10 s; now ~2°, inside the p.9 target of 5°.
8. **Message fields carry unit suffixes and `"v": 1`** (repo convention): `t` → `t_s`,
   `battery` → `battery_pct`.

An independent review then found more, now fixed with regression tests: isolated
camera misreads could snap yaw (the 45° gate now needs *consecutive* frames); a gyro
mirrored relative to the accelerometer broke "up" tracking (new `gyro_sign`, detected
by the axis experiment); 150–180° snap turns took 1.05 s to recover (now 0.17 s); and
the Enter re-zero wasn't recorded, so replay differed from live (one shared
`YawPipeline` now drives live, replay and analysis).

## Layout

```
headmotion/   frame, sources (BrainFlow + replay), yaw (estimator + YawPipeline), contact, health,
              messages, bus (MQTT), cli, calibration, synth (fake Muse + camera), contact_sim, analysis
scripts/      record, check_stream, live_yaw, analyze, simulate_contact, make_fake_data
dashboard/    live dashboard: Flask app (app.py), templates/index.html, static/app.js + style.css
tests/        120 tests; each maps to a guide "check", "DONE WHEN" or review finding
docs/         Windows setup + troubleshooting, streaming recipe, head-yaw notes, contact maths, experiments tracker,
              agentic-coding notes (pstack)
calibration.json  measured constants; every value is a PLACEHOLDER until its experiment runs
decisions.tsv     decision log (pstack show-me-your-work format)
```
