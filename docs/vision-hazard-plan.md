# Plan: vision-based hazard direction

**Goal:** from the overhead camera, detect the worker and potential hazards (the RC machine and other
vehicles), track them, and report for each hazard **where it is relative to the worker**: its direction,
its distance, and whether and when it will reach them. Combined with the Muse head yaw, this answers
"is a hazard closing in that the worker isn't looking at?"

Code: `vision/` (first built on the `max/vision-hazard-direction` branch). This overlaps with Zoe's area (detection and tracking) and
Hanh's (floor mapping and ArUco), so share the plan with both.

## Status

| Part | State |
|---|---|
| Floor frame + homography from ArUco markers (angled camera) | **Built**, tested on a simulated angled camera; not yet checked on the real floor |
| Angle maths: bearing, distance, relative-to-head bearing | **Built** and unit-tested |
| YOLO26 person + vehicle detection with ByteTrack IDs | **Built**; live webcam run tracked a person with a stable ID; RC car not yet tested |
| Worker choice + hazard distance/angles per frame (`scene.py`) | **Built** and unit-tested |
| Live tools: `check_floor` (calibration check), `run_vision` (detection + angles) | **Built** |
| Phone camera input: `run_phone` runs the pipeline on Sean's WebRTC phone stream (`stream_data/`) | **Built**; live run tracked a person and found all 4 floor markers |
| Toy car: YOLO vehicles at a lower threshold, plus an ArUco tag on the car (`--car-marker 4`) | **Built** and unit-tested; not yet run with the real toy car |
| Hazard message (`sightline/vision/hazards` format below), streamed live at `/hazards` (SSE), optional `--mqtt` | **Built** (no velocities / CPA fields yet) |
| Velocities, time to contact in the live app | Not yet: needs per-track velocities, which then feed `max/headmotion/contact.py` |
| JSONL log, Muse head yaw link, evaluation | Not yet |

How to run and test each piece: [vision-testing.md](vision-testing.md).

---

## 1. Coordinate system (recommendation)

**Compute everything in the shared floor frame from `CLAUDE.md`,** not in image pixels:

- Floor frame: metres, origin at floor marker 0, +x toward marker 1, +y toward marker 3.
- Angles: degrees, 0° along +x, CCW positive seen from above, wrapped to [-180, 180).
- Time: `pylsl.local_clock()` seconds at frame capture.

Why not image-space angles: pixels aren't metres, so there would be no distance, speed or
time-to-contact. Unless the camera points straight down, perspective also bends the angles.
The floor frame is also what the Muse heading is already zeroed to (tare while facing +x), so the
camera and the headband agree without any extra conversion.

The floor frame is a grid laid on the real floor, marked out by printed ArUco markers taped down at
tape-measured spots:

```
        +y
         ▲
 marker 3 ■ · · · · · · · ■ marker 2        (e.g. a 3 m × 2 m area)
         ·                ·
         ·    floor area  ·
         ·                ·
 marker 0 ■ · · · · · · · ■ marker 1  ──▶ +x
   (0,0)                (3,0)
```

### Why angles can't be measured in the image

The camera points at an angle, so the image is a perspective view: parallel floor lines converge, and
a metre near the camera covers far more pixels than a metre at the back. An angle drawn between two
objects in the image isn't their real angle on the floor, and pixel distances aren't real distances.

```
 what the camera sees                    the real floor, from above
  ┌──────────────────┐                   ┌──────────────────┐
  │   ■──────────■   │  ← far side       │ ■              ■ │
  │  /            \  │    looks small    │                  │
  │ /              \ │                   │                  │
  │■────────────────■│  ← near side      │ ■              ■ │
  └──────────────────┘    looks big      └──────────────────┘
```

### Step 1: pixels → floor metres (homography)

Any flat plane seen by a camera follows a fixed perspective mapping, a 3×3 matrix **H** (a
homography). With H, any pixel on the floor converts to its real (x, y), which is like viewing the
image from directly above.

1. OpenCV detects the floor markers and their 4 corners in the image.
2. The real floor position of each corner comes from `floor_layout.json` (tape-measured centres + printed size).
3. `cv2.findHomography` fits H from the 16 pixel ↔ floor pairs. The leftover error per marker is reported,
   and a large error means a marker was measured wrong or laid down rotated.

```
[x', y', w] = H · [u, v, 1]
x = x'/w,  y = y'/w            → metres on the floor
```

Calibrate once per camera position, and again if the camera is bumped. Wide-angle webcams also bend
straight lines (lens distortion); if checks show errors growing toward the image edges, add a
one-time checkerboard lens calibration and correct pixels for it before applying H.

### Step 2: use the point where each object touches the floor

H is only correct for points **on the floor**. With an angled camera, a person's head (about 1.7 m up)
maps to a floor spot well behind them, so objects are placed by their ground-contact point:

```
 camera
   ◉
    \  ╲
     \   ╲  ray through the head
      \    ╲
  ray  \    o  head (1.7 m up)
 through\   |
  feet   \  |  person
          \ |
 ──────────●─────────────✕──── floor
         feet          where the head's pixel
      (correct)        lands on the floor (wrong)
```

| Object | Point used | Note |
|---|---|---|
| Person | bounding-box bottom-centre (feet) | correct |
| RC car | bounding-box bottom-centre | this is the edge nearest the camera, a few cm from the car's centre; an ArUco tag on the car gives its exact centre and heading |

### Step 3: angle from worker to hazard

With the worker at W = (xw, yw) and the hazard at P = (xh, yh), both in floor metres:

```
dx = xh − xw,   dy = yh − yw
distance_m  = √(dx² + dy²)
bearing_deg = atan2(dy, dx) in degrees      (0° = +x, counter-clockwise positive)
```

Worker at (1.0, 1.0):

| Car at | `bearing_deg` | In words |
|---|---|---|
| (2.0, 1.0) | 0° | toward +x (marker 1) |
| (1.0, 2.0) | +90° | toward +y (marker 3) |
| (0.0, 1.0) | −180° | toward −x |
| (2.0, 2.0) | +45° | diagonally toward marker 2 |

### Step 4: relative to where the worker is looking

**Two directions per hazard:**

| Field | Frame | Meaning | Needs Muse? |
|---|---|---|---|
| `bearing_deg` | floor (allocentric) | direction from worker to hazard, 0° = +x, CCW + | No |
| `rel_bearing_deg` | worker's head (egocentric) | `wrap(bearing_deg − head_yaw_deg)`: 0 = straight ahead, +90 = to their left, ±180 = behind | Yes |

The vision module publishes the floor-frame bearing. The fusion step subtracts the latest head yaw.
This keeps the vision code independent of the Muse, and either side can be tested alone.

Worker facing +y (head yaw +90°):

| Car `bearing_deg` | `rel_bearing_deg` | Where the worker sees it |
|---|---|---|
| +90° | 0° | straight ahead |
| −180° | +90° | on their left |
| 0° | −90° | on their right |
| −90° | −180° | behind them, so it's the dangerous one |

"Hasn't looked at it" = |`rel_bearing_deg`| outside the worker's field of view (e.g. ±60°) while the hazard closes in.

### Accuracy

The angle error depends on how far apart the worker and hazard are: a 10 cm position error is about
6° at 1 m apart but about 2° at 3 m. The angle is noisiest when the hazard is close, which is
when it matters most. That's why tracks are smoothed over time and the floor mapping is checked
against tape-measured spots (target: within 10 cm).

### Testing the floor mapping and angles

See [vision-testing.md](vision-testing.md).

---

## 2. Pipeline

```
 camera frame ──▶ detect (YOLO26) ──▶ track (ByteTrack) ──▶ image → floor (homography)
  t = local_clock()   person, vehicle     stable track IDs        ground point in metres
                                                                         │
                                                                         ▼
                                                     smooth position + velocity per track
                                                                         │
                                                                         ▼
                                     hazard geometry (worker vs each hazard track):
                                     distance, bearing, closing speed, time to closest approach
                                                                         │
                       ┌─────────────────────────────────────────────────┤
                       ▼                                                 ▼
          MQTT  sightline/vision/hazards                       JSONL log (replay, evaluation)
                       │
                       ▼
     fusion: rel_bearing = bearing − Muse head yaw → "unseen + closing" → wristband buzz
```

### Hazard geometry

`vision/hazards.py` gives each hazard's `distance_m`, `bearing_deg = atan2(p.y, p.x)` (wrapped) and
`rel_bearing_deg = bearing_deg − head yaw`, with `p` = hazard position − worker position (floor frame).

Time to contact is **not** computed here. `max/headmotion/contact.py` already does it (constant-velocity
closest approach, the time the gap first reaches the radius, and hysteresis so the flag doesn't flicker),
and publishes `sightline/contact`. Once tracking gives velocities, vision feeds that one implementation
instead of keeping a second copy. See [max/docs/contact-maths.md](../max/docs/contact-maths.md).

---

## 3. Code layout

```
vision/
├── floor.py         [built]  ArUco detection, homography fit, pixel ↔ floor metres, save/load floor_calib.json,
│                             per-marker fit error, ground_point() = bottom-centre of a box
├── hazards.py       [built]  pure geometry: bearing, distance, relative bearing, in_view()
├── detector.py      [built]  YOLO26 wrapper (stock COCO weights), person / vehicle kinds, ByteTrack track IDs,
│                             separate vehicle threshold; Detection.ground overrides the box-bottom floor point
├── scene.py         [built]  per frame: pick the worker (sticky track ID), place everyone on the floor,
│                             distance + bearing + relative bearing of each vehicle, nearest first
├── overlay.py       [built]  shared drawing: labels, floor grid, axes, head arrow
├── make_markers.py  [built]  printable ArUco markers
├── check_floor.py   [built]  live calibration check: grid overlay, click to measure positions / angles
├── pipeline.py      [built]  VisionPipeline: one frame → markers (once) → floor map → YOLO → car tags → scene;
│                             overlay drawing; scene_to_dict() = the hazard message below
├── run_vision.py    [built]  live app: camera or --video → pipeline, overlay + console
├── run_phone.py     [built]  same pipeline on the phone stream (stream_data/stream_server.py), overlay window,
│                             /hazards SSE stream + optional MQTT (HazardHub)
├── tracks.py        [todo]   per-track smoothed position + velocity (Kalman) → feeds max/headmotion/contact.py
└── (JSONL log)      [todo]
vision/tests/
├── test_hazards.py  bearing tables, relative bearing, wrapping, field of view
├── test_floor.py    simulated angled camera: detection, calibration accuracy, head-vs-feet, rotated marker
├── test_scene.py    worker choice, floor placement, hazard angles; one real YOLO run on a sample photo
└── test_phone.py    phone model messages, toy-car marker tag, tag-vs-YOLO merge, /hazards stream
stream_data/                Sean's phone camera → WebRTC server (merged from sean/phone-camera-stream)
floor_layout.example.json   template for the real marker layout
requirements.txt            one file for the whole repo (vision, phone stream, Muse, dashboard, phone alert)
```

### Data flow in `run_vision.py` (per frame)

1. Read a frame (webcam via DirectShow, or `--video`).
2. Floor map: load `--calib`, or re-fit from the markers every frame with `--layout` (`f` freezes it).
   With neither, the app runs in pixels-only mode: boxes and IDs, no metres or angles.
3. `Detector`: YOLO26 + ByteTrack → `Detection(kind, label, conf, box, track_id)`; ground point =
   bottom-centre of the box.
4. `build_scene`: worker = the same track ID as last frame if still visible, else the most confident
   person. Each vehicle → `HazardReading(distance_m, bearing_deg, rel_bearing_deg)` using the head yaw
   (simulated with `a` / `d` until the Muse is wired in).
5. Draw the overlay and print a one-line summary every `--print-every` seconds.

`hazards.py`, `floor.py` and `scene.py` have no camera or model dependency, so they're unit-tested
without hardware. They're also good `claude-ok` tasks to extend.

### Output message (draft, agree with Jordan's message contract)

Topic `sightline/vision/hazards`, one message per processed frame. **What `run_phone` sends today**
(no velocities yet; `x_m`/`y_m`/angles are `null` without a floor map, `rel_bearing_deg` uses the
simulated head yaw):

```json
{
  "v": 1, "t_s": 519300.21, "frame": 412, "calib": "live calib, all markers", "infer_s": 0.0382,
  "head_yaw_deg": 0.0,
  "worker": {"track_id": 3, "x_m": 1.02, "y_m": 0.95, "conf": 0.88},
  "hazards": [
    {"track_id": 4, "kind": "vehicle", "label": "toy car", "source": "marker", "conf": 1.0,
     "x_m": 2.5, "y_m": 0.5, "distance_m": 1.55, "bearing_deg": -17.0, "rel_bearing_deg": -17.0}
  ]
}
```

The full target message, once tracking gives velocities:

```json
{
  "v": 1,
  "t_s": 451239.051,
  "frame": 1832,
  "worker": {"track_id": 3, "x_m": 1.42, "y_m": 0.88, "vx_mps": 0.10, "vy_mps": 0.00, "conf": 0.91},
  "hazards": [
    {"track_id": 7, "kind": "vehicle", "x_m": 2.90, "y_m": 0.70, "vx_mps": -0.60, "vy_mps": 0.05,
     "distance_m": 1.49, "bearing_deg": -6.9, "conf": 0.84}
  ]
}
```

Time to contact for the machine goes out separately as `sightline/contact`, from `max/headmotion/contact.py`.

`worker: null` when no worker is seen. Fusion treats a stale or missing vision message as **unsafe**
(fail-safe), the same way it should treat a stale head yaw.

---

## 4. Phases

| # | Phase | Done when | Needs hardware | Status |
|---|---|---|---|---|
| 0 | **Day-one feasibility test** | Recorded a 1-min clip of a person + the RC machine; logged whether stock YOLO26 detects each and at what FPS on the demo laptop | Camera, RC machine | Person: detected and tracked live (stable ID over ~30 s). RC car: **not tested yet**. CPU speed: ~50 ms per frame on Ultralytics' sample photo |
| 1 | Geometry core (`hazards.py`, tests) | Tests pass for head-on, crossing, receding, stationary and "already inside radius" cases | No | **Done** |
| 2 | Floor mapping (`floor.py`) | Homography from the 4 floor markers; points at tape-measured spots land within 10 cm | Camera, markers | Code + simulated-camera tests done; real-floor check waiting on markers |
| 3 | Detection + tracking | Worker and machine keep stable track IDs through a 60 s clip; FPS and per-frame latency logged | Camera | Code done (`detector.py`, `scene.py`, `run_vision.py`; FPS + YOLO ms shown live); machine not yet tested |
| 4 | Kinematics | Velocity of the machine driven along a measured line is within 20% of tape-and-stopwatch speed | Camera, RC machine | Not started |
| 5 | Messages + replay | `run_vision.py --video clip.mp4 --log` produces the JSONL above; same output live and replayed | No (uses phase 0 clip) | `--video` replay works; messages + log not started |
| 6 | Fusion with head yaw | `rel_bearing_deg` matches reality as the worker turns (Muse tared to +x); "unseen + closing" flag correct on scripted runs | Camera, Muse | Relative bearing computed from a simulated yaw; Muse link not started |
| 7 | Evaluation + tuning | Scripted scenarios (approach from behind, from the side, in view, passing by) scored for correct / missed / false alerts and warning time | Everything | Not started |

Phase 0 comes first because it decides the detection approach (below). Phases 1 and 5 can run in
parallel with the hardware work.

---

## 5. Risks and fallbacks

| Risk | Why it matters | Fallback |
|---|---|---|
| Stock YOLO (COCO) doesn't detect the **RC machine** as a vehicle, especially from above | No hazard = no alert | (a) fine-tune YOLO26n on a few hundred labelled frames (start from the Roboflow ForkliftProject); (b) put an **ArUco tag on the machine**, which also gives its heading for free |
| Top-down **person** detection is weaker (COCO is mostly side views) | Worker lost, or position noisy | Angle the camera instead of straight down; fine-tune; or an ArUco tag on the hardhat |
| More than one person in view | Which one is the worker? | Hardhat tag, or "the tracked person nearest the start position"; for the demo, keep others out of frame |
| Box jitter → noisy velocity → false TTC | False alerts | Smooth with a constant-velocity Kalman filter per track; require a threat for N consecutive frames |
| Latency (camera + model + network) eats the warning time | Late buzz | Use the smallest model that's accurate enough; measure end-to-end latency in phase 3; target under ~200 ms for vision (a goal, not yet measured) |
| Muse heading drifts away from the floor frame | Wrong `rel_bearing_deg` | Re-tare at the start of each run; later, correct drift with a vision heading (hardhat tag, Hanh's area) |

**Vision-based head direction:** Hanh's guide covers head direction from the hardhat. An ArUco tag on
the hardhat would give an absolute head yaw in the floor frame, which is exactly what the Muse lacks.
Even at a low rate it could correct Muse drift continuously. It's worth deciding early whether that's
in scope.

---

## 6. Open questions for the team

1. **Camera mount:** angled (decided). Still open: mount height, and does the whole floor area fit in frame
   with all 4 markers visible?
2. **Markers on objects:** may the RC machine and hardhat carry ArUco tags? This makes phases 3 and 6
   much more robust.
3. **Message contract:** topic name and fields with Jordan; who runs fusion (laptop or wristband)?
4. **Demo laptop:** GPU or CPU only? This decides the model size (YOLO26n vs s).
5. **Hazard classes:** just the RC machine, or also people pushing carts, other vehicles, static
   hazards (marked zones)?
6. **Ownership:** split with Zoe (detection/tracking) and Hanh (floor mapping) so this doesn't duplicate their work.

## Dependencies

In the repo-wide `requirements.txt`: `numpy`, `opencv-contrib-python` (ArUco), `ultralytics` (YOLO26 +
ByteTrack, pulls in PyTorch, CPU build by default), `pytest`, `paho-mqtt` (messages), `pylsl` (project clock),
plus the phone-stream packages (`aiortc`, `aiohttp`, `cryptography`, `pillow`).

Ultralytics also installs plain `opencv-python`, which shares files with the contrib build; see
[vision-testing.md](vision-testing.md#setup) if ArUco disappears after an install.
