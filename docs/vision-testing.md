# Testing the floor mapping and hazard angles

What exists so far (branch `max/vision-hazard-direction`), and how to test each piece. Not built yet:
velocities / time to contact, MQTT output, and the Muse link; see [vision-hazard-plan.md](vision-hazard-plan.md).

| File | What it does |
|---|---|
| `vision/hazards.py` | Angle maths: bearing, distance, relative-to-head bearing, closest approach / time to contact |
| `vision/floor.py` | Detects floor ArUco markers, fits the pixel → floor-metres homography, ground point of a box |
| `vision/make_markers.py` | Generates printable markers |
| `vision/check_floor.py` | Live tool: grid overlay on the camera image, click to measure positions and angles |
| `vision/detector.py` | YOLO26 (stock COCO weights) person + vehicle detection with ByteTrack track IDs |
| `vision/scene.py` | Picks the worker, places everyone on the floor, distance / bearing / relative bearing of each vehicle |
| `vision/run_vision.py` | Live app: detection + floor map + hazard angles on the camera feed |
| `vision/overlay.py` | Shared drawing helpers |
| `tests/test_hazards.py`, `tests/test_floor.py`, `tests/test_scene.py` | Unit tests, including a simulated angled camera and one real YOLO run |

## Setup

```powershell
cd sight-line
python -m venv .venv
.venv\Scripts\pip install -r requirements-vision.txt
```

Ultralytics also installs plain `opencv-python`, which shares files with `opencv-contrib-python`.
If `import cv2; cv2.aruco` fails afterwards, reinstall the contrib build:
`.venv\Scripts\pip uninstall -y opencv-python opencv-contrib-python` then
`.venv\Scripts\pip install opencv-contrib-python`.

The YOLO weights (`yolo26n.pt`, ~5 MB) download automatically on first use and are gitignored.

## Level 1: unit tests (no hardware, 1 minute)

```powershell
.venv\Scripts\python -m pytest tests -v
```

| Test file | Checks |
|---|---|
| `test_hazards.py` | Every row of the bearing tables in the plan; head-on, passing, receding, parked and walking-into-a-parked-car cases for time to contact |
| `test_scene.py` | Fake detections seen by the simulated camera: worker floor position, distance/bearing/relative bearing, nearest-first sorting, sticky worker choice; plus YOLO on Ultralytics' sample photo (must find people and the bus) |
| `test_floor.py` | Draws the 4 markers on a virtual floor and photographs them with a simulated webcam 1.8 m up, tilted about 36° down. Then checks markers are found, corners fit within 1 cm, any floor point maps back within 2 cm, a head-height point lands > 1 m off (why feet are used), and a rotated marker is flagged |

All should pass. If you change the maths, these are the safety net.

## Level 2: real camera, real floor (about 30 minutes)

### 1. Make and lay out the markers

```powershell
.venv\Scripts\python -m vision.make_markers          # writes data/markers/marker_0..3.png
```

1. Print each at 100% / "actual size". Bigger is easier to detect from far away: aim for a black
   square of at least 15 cm, more if the camera is far from the floor.
2. Measure the black square with a ruler (not including the white border).
3. Tape the markers flat on the floor at the corners of a rectangle, e.g. 3 m × 2 m:
   marker 0 at the origin, 1 along +x, 3 along +y, 2 in the far corner.
4. Lay every marker with the **top edge of the printout facing +y** (toward marker 3). The label
   under each marker says so.
5. Measure where each marker's **centre** is with a tape measure.
6. Copy `floor_layout.example.json` to `floor_layout.json` and fill in the measured size and centres.

### 2. Set up the camera

Mount it at its real angle and height. All 4 markers must be fully in frame; a marker cut off at
the edge isn't detected. Close the Camera app and anything else using the webcam.

### 3. Run the checker

```powershell
.venv\Scripts\python -m vision.check_floor --layout floor_layout.json
```

Add `--camera 1` if the wrong camera opens.

| Check | How | Pass |
|---|---|---|
| Markers found | Top bar says "all markers seen"; each marker outlined | all 4, every frame |
| Fit error | Number next to each marker | under ~1–2 cm each. A yellow label (> 3 cm) usually means a wrong centre measurement or a rotated marker |
| Grid lines up | The grey 0.5 m grid and red +x / green +y arrows drawn on the floor | grid follows tape lines/tiles; arrows point from marker 0 toward markers 1 and 3 |
| Positions | Put tape crosses at a few measured spots (e.g. (1.0, 1.0), (2.5, 0.5), something near the far edge). Left-click each one | printed (x, y) within 10 cm of the tape measure |
| Angles | Right-click a spot (the "worker"), left-click others. Compare `bearing` with the expected value (e.g. from (1,1) to (2,2) → +45°) | within a few degrees |
| Relative to head | Press `a` / `d` to turn the simulated head (yellow arrow); `rel` changes | 0 when the arrow points at the clicked point, +90 when the point is to the arrow's left |
| A person | Stand on a tape cross; right-click your **feet** in the image | within 10 cm. Click your head instead to see how far off it lands |

Press `s` to save the calibration to `floor_calib.json` once it looks right, `f` to freeze it so it
stops re-fitting each frame, and `q` to quit.

If errors grow toward the edges of the image even with correct measurements, the webcam lens is
distorting the image. That needs a one-time checkerboard lens calibration, which isn't built yet.

## Level 2b: live detection

```powershell
.venv\Scripts\python -m vision.run_vision                                # no markers: boxes + IDs only
.venv\Scripts\python -m vision.run_vision --layout floor_layout.json     # with markers: metres + angles
.venv\Scripts\python -m vision.run_vision --calib floor_calib.json --layout floor_layout.json
.venv\Scripts\python -m vision.run_vision --video clip.mp4               # recorded clip
```

| Colour | Meaning |
|---|---|
| Yellow box + dot | the worker (tracked person) and their feet; yellow arrow = simulated head yaw (`a` / `d`) |
| Orange box + dot | a vehicle and its ground point; label = distance, `brg` (floor bearing), `rel` (relative to head) |
| Green box | other people |
| Red line | worker → vehicle |

| Check | How | Pass |
|---|---|---|
| Detection | Walk through the frame; roll the RC car through it | worker gets a yellow box; car gets an orange box. **If the RC car isn't detected, that's the key day-one finding**: try `--conf 0.2`, then plan fine-tuning or an ArUco tag on the car |
| Tracking | Walk around for a minute | track ID stays the same |
| Speed | Top bar | FPS and YOLO ms (about 50 ms per frame on this laptop's CPU for the sample image) |
| Distance / angle | With markers: stand on a tape cross, park the car on another | distance and `brg` match the tape-measured values; `rel` = 0 when the yellow arrow points at the car |

Console prints a one-line summary every second (`--print-every`). Only the COCO classes in
`--vehicles` count as hazards (default: bicycle, bus, car, motorcycle, train, truck).

## Level 3: with the Muse (later)

Once fusion exists: tare the Muse while facing +x (toward marker 1), then compare the live head yaw
with the yellow head arrow, and `rel_bearing_deg` with where the hazard really is.

## What isn't testable yet

Velocities and time to contact, the MQTT output, and live head yaw from the Muse. Those come next in
the plan. The most useful test right now is the day-one one: does stock YOLO26 see the RC car from
this camera angle?
