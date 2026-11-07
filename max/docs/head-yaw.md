# Head yaw: how it works (guide p.4–6, p.9)

Goal: which way is the worker's head pointing on the floor frame, ~50 Hz, accurate to
±10° at the moment of an alert (the rule checks ±30°).

## Per gyro sample

```
raw gyro w (°/s, Muse axes, de-jittered local_clock() timestamps from BrainFlow)
   − bias                    5 s still at start; auto re-zeroed after 3 s still; nudged by the camera
   × gyro_sign × scale       handedness vs accelerometer, scale error (calibration.json)
   rotate orientation R      full 3-D head orientation, tilt pulled toward the accelerometer
   heading of nose axis      yaw = sign × heading(R · nose) + offset   (nose from the axis experiment)
     (until nose is measured: ∫ (w · up) dt, the turn rate about TRUE vertical)
   gaps ≥ 0.1 s              orientation held: a dropped packet is not a turn
   + K · camera error        when Hanh's marker reads cleanly
 = yaw
```

## Why the dot product fixes tilt

Looking down 40°, the band's "yaw axis" is tilted 40° too, so it only sees cos 40° =
77% of a body turn: a 90° turn reads 69°. Projecting the full 3-axis rotation onto
"up" measures the rotation about the real vertical, so it reads 90° in any head pose.
On synthetic data: 90.0° (fix) vs 68.9° (axis only).

## Where "up" comes from

The accelerometer says which way is down, but only when the head isn't accelerating.
Turning your head swings the band on a ~10 cm lever around your neck, which adds about
0.1 g at 200 °/s. The guide averages the accelerometer slowly to ignore that, but then
"up" lags a nod by ~1 s, and any turn made during the nod is mis-measured.

`Orientation` does both: every gyro sample it **rotates** the head orientation by the
measured rotation (fast, no lag), and it **nudges** its tilt 2% toward the accelerometer
(slow, so drift in the rotation can't build up). On 2 min of synthetic free movement: 11° p95
error with the accelerometer average alone, 2.4° with this.

Rotating "up" needs the gyro and accelerometer to agree on handedness. If the gyro is
mirrored (the Athena's gyro is negated relative to older bands, for example), "up"
rotates the wrong way during nods: 18° p95. The output `sign` can't fix that, so there
is a separate `gyro_sign`. `analyze.py axis` finds it by rotating "up" both ways
through the nods and keeping the sign that stays in agreement with the accelerometer.

## Why the nose axis (and not just "rate about vertical")

Integrating the rate about vertical is exact for turns and nods, but not for a
combination: look down 40°, tilt ear-to-shoulder 20°, come back, and the integral is
off by ~13° per cycle (rotations don't commute), with nothing to pull it back. What
Hanh's camera sees, and what the alert rule needs, is the **heading of the nose**.
With the full orientation, that is just the floor-frame direction of the band's
forward axis: bounded, no accumulation.

The forward axis comes from the axis experiment (p.4a), which already has nods and
tilts: a nod turns about the ear-to-ear axis, a tilt about the nose, so
nose = ear × up, cross-checked against the tilts (`nose_check_deg`, ~0° on synthetic
data). Its sign doesn't matter: the start-of-run reset absorbs a 180° offset. A nose
axis 10° wrong still gives < 3° p95 on 2 min of free movement. While the nose points
nearly straight up or down (> ~75°) its heading is undefined, so yaw falls back to the
vertical-rate integral until it comes back. Until `nose_axis` is in `calibration.json`,
the vertical-rate integral is used throughout.

## Bias

- At the start of each run, 5 s still; `measure_bias` refuses if they moved (std > 2 °/s).
- `StillRebias`: whenever the head has been still for 3 s, re-measure. "Still" means the
  gyro's std is under `rebias_max_std_dps` (0.6 °/s placeholder): set it from the D2
  worn-still recording with `analyze.py drift --save` (2× the measured still noise). If
  our band is noisier than the threshold, re-bias would silently never fire.
  **Guard:** only accept a new bias within 0.75 °/s of the old one. A slow, steady turn
  also has low jitter; without the guard it would be absorbed into the bias.
- **From the camera:** see "Camera blend" below.

## Resets (fallback when the camera can't see the hardhat)

- **Start of run:** worker faces +x, operator presses Enter → `est.reset(0)`.
- **Reference glance:** a sign at a known bearing. Still and within 10° of it for 1.5 s →
  snap to the bearing. Fires once per dwell. It can only fix drift smaller than 10°;
  bigger drift never enters the cone.

## Camera blend

Hanh's yaw is absolute but late (~0.1 s) and sometimes missing. For each clean frame:
look up what our yaw was `LAG` seconds before the frame's timestamp, compute the error,
and move `K` of the way toward the camera.

- **Bias learning (integral term):** the same error also nudges the gyro bias along
  "up" (`blend_ki` = 0.003 °/s per degree per frame, about a 20 s time constant). The bias
  creeps as the band warms; a pull on yaw alone keeps yaw right while the camera sees
  the marker but leaves the bias wrong, so a blind spot late in a session drifted ~9° in
  10 s on synthetic data (0.3 °/s per minute creep). With it: ~2°. Skipped during clip
  recovery, where the error is lost angle, not bias.
- **History shift:** after a correction, the stored history moves too. Otherwise the next
  frames compare against stale history and pull for the same error again.
- **45° gate:** bigger errors are probably misreads and are ignored, unless the same big
  error (within 15°) persists over **consecutive** frames (no gap over 0.2 s) for 1 s:
  a real turn while hidden. Then we snap to it. Two isolated misreads far apart don't count.
- **After clipping:** the gyro tops out at ~245 °/s, and a snap turn loses 30–120° of
  angle. For 1 s after a clip, K = 0.2 instead of 0.05, and the gate accepts a
  consistent big error after 0.15 s instead of 1 s. Recovery: about 0.1–0.2 s.

## Published message

`HeadYaw(t_s, yaw_deg, rate_dps, source, cam_age_s, valid)`, published as JSON with
`"v": 1` on `sightline/head_yaw` (`live_yaw.py --mqtt HOST`). `source` is "blend" while a
camera correction landed in the last 2 s. `valid` is False until bias + "up" + yaw are
set, after a gap, while a big camera disagreement is waiting at the gate, and while
a clip is being repaired with the camera in view.

## One loop for live, replay and analysis

`YawPipeline.feed(sample)` handles the bias window, the start-of-run reset, operator
re-zeros (recorded as `RESET` samples) and routing. `live_yaw.py`, replay and every
analysis use it, so a recording replays exactly as it ran live.
