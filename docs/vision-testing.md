# Testing the floor mapping and hazard angles

What exists so far in `vision/` and `stream_data/`, and how to test each piece. Not built yet:
velocities / time to contact, and blending the Muse heading with the camera; see [vision-hazard-plan.md](vision-hazard-plan.md).

| File | What it does |
|---|---|
| `vision/hazards.py` | Angle maths: bearing, distance, relative-to-head bearing (time to contact lives in `max/headmotion/contact.py`) |
| `vision/floor.py` | Detects floor ArUco markers, fits the pixel → floor-metres homography, ground point of a box |
| `vision/make_markers.py` | Generates printable markers |
| `vision/check_floor.py` | Live tool: grid overlay on the camera image, click to measure positions and angles |
| `vision/detector.py` | YOLO26 (stock COCO weights) person + vehicle detection with ByteTrack track IDs |
| `vision/scene.py` | Picks the worker, places everyone on the floor, distance / bearing / relative bearing of each vehicle |
| `vision/run_vision.py` | Live app: detection + floor map + hazard angles on the camera feed |
| `vision/overlay.py` | Shared drawing helpers, including the angle panel (heading vs. hazard, FACING / NOT FACING) |
| `vision/run_phone.py` | Live app on the phone's camera stream (Level 2c) |
| `vision/head_yaw.py` | Worker heading from the Muse S: runs `max/headmotion` (Max's BrainFlow → yaw code) in a background thread (Level 3) |
| `vision/tests/test_hazards.py`, `test_floor.py`, `test_scene.py`, `test_phone.py` | Unit tests, including a simulated angled camera and one real YOLO run |

## Setup

```powershell
cd sight-line
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Ultralytics also installs plain `opencv-python`, which shares files with `opencv-contrib-python`.
If `import cv2; cv2.aruco` fails afterwards, reinstall the contrib build:
`.venv\Scripts\pip uninstall -y opencv-python opencv-contrib-python` then
`.venv\Scripts\pip install opencv-contrib-python`.

The YOLO weights (`yolo26n.pt`, ~5 MB) download automatically on first use and are gitignored.

## Level 1: unit tests (no hardware, 1 minute)

```powershell
.venv\Scripts\python -m pytest vision/tests -v
```

| Test file | Checks |
|---|---|
| `test_hazards.py` | Every row of the bearing tables in the plan, angle wrapping and the field of view |
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

## Level 2c: phone camera + toy car

The phone is the camera: Sean's `stream_data/` server receives its video over WebRTC and
`vision.run_phone` runs the same pipeline on every frame.

```powershell
.venv\Scripts\python -m vision.run_phone --layout floor_layout.json --car-marker 4
```

1. Phone and laptop on the **same Wi-Fi**. Campus networks often block device-to-device traffic:
   use the phone's hotspot instead.
2. Open the printed `https://<laptop IP>:8443` in **Chrome** on the phone, tap **Advanced → Proceed**
   (self-signed certificate), then **Start streaming** and allow the camera.
3. The laptop opens a **SightLine phone** window with the overlay (same colours as Level 2b).
   Keys: `a` / `d` head yaw, `z` re-zero the Muse heading, `f` freeze calibration, `s` save it.
   `q` hides the window; reopen it at `https://localhost:8443/window`.

**Wrong IP printed?** The server prints the first network address it finds. With a VPN on (e.g.
ProtonVPN), that's the VPN's address and the phone can't reach it. Find the Wi-Fi address with
`ipconfig` (the "Wireless LAN adapter Wi-Fi" IPv4 line) and open `https://<that IP>:8443` instead.
If the page loads but video never connects, turn the VPN off (or allow LAN access in its settings)
and allow Python through Windows Firewall.

| Where | What |
|---|---|
| `https://localhost:8443/hazards` | **live angle stream** (Server-Sent Events): one JSON message per frame, see the message in [vision-hazard-plan.md](vision-hazard-plan.md#output-message-draft-agree-with-jordans-message-contract) |
| phone page | the same message, over the WebRTC data channel |
| terminal | one-line summary per second: worker position, each vehicle's distance, bearing, relative bearing |
| `https://localhost:8443/view` | raw phone video with boxes (no floor grid) |
| `--mqtt HOST` | also publish on `sightline/vision/hazards` (needs a broker, e.g. Mosquitto) |

**Toy car.** Stock YOLO often misses toy cars, especially from above. Two ways in, both on at once:
- YOLO's vehicle classes, with their own threshold `--vehicle-conf` (default 0.25; lower also needs `--no-track`,
  because ByteTrack only starts tracks above about 0.25).
- **A marker tag on the car (recommended):** print an ID that isn't a floor marker
  (`python -m vision.make_markers --ids 4`), about 5-8 cm, keep the white border, tape it flat on the roof,
  and run with `--car-marker 4`. The tag's centre is the car's floor position (a few cm off because the
  roof is above the floor). A YOLO vehicle box around the tag is replaced by the tag.

**If the laptop's IP changes** (new Wi-Fi), restart with `--new-cert` and open the new address on the phone.

**Only the worker and the car (`--tags-only`).** YOLO then looks for people only, so other objects
(a toy truck, a chair read as a bus) are never drawn or reported as hazards. Vehicles come only from
`--car-marker` tags, and other people are hidden. Floor markers are outlined in cyan with their id.

```powershell
.venv\Scripts\python -m vision.run_phone --layout floor_layout.json --car-marker 42 --tags-only
```

**Angle panel** (top right of the overlay window): a top-down dial in the floor frame (+x right,
+y up) with the worker's heading (yellow arrow), the direction from the worker to the car (orange
arrow) and the facing cone (shaded). Below it: worker heading, car bearing, relative angle (+ = car
to the worker's left), and **FACING** (green) when |relative angle| ≤ `--facing-deg` (default 30),
else **NOT FACING** (red). Each hazard in `/hazards` also gets `"facing": true / false / null`.
Without `--muse`, the heading is the one you set with `a` / `d`.

## Level 3: worker heading from the Muse S

The worker wears the Muse S. `--muse` reads its gyro over Bluetooth with BrainFlow and turns it into
a heading on the floor frame (Max's code in `max/headmotion`: bias removal, tilt correction, yaw).
That heading replaces the `a` / `d` arrow, so the angle panel says whether the worker is really
facing the car.

```powershell
.venv\Scripts\pip install -r requirements.txt          # includes brainflow
.venv\Scripts\python -m vision.run_phone --layout floor_layout.json --car-marker 42 --tags-only --muse
```

1. **Before starting:** switch the Muse S on, close the Muse phone app, and **don't pair it in Windows
   Bluetooth settings** (remove it there if it is). BrainFlow connects to it directly, and only one
   program can hold it. Keep it near the laptop.
2. **Connecting:** the terminal prints `[muse] opening muse`. If the band isn't found it says so and
   retries every few seconds; the panel shows `heading: muse: not found, retrying`. Once the band is
   on it connects by itself, no restart needed.
3. **Calibrating (5 s):** on `[muse] connected`, the worker stands **completely still facing +x**
   (toward floor marker 1). This measures the gyro bias. Any movement (more than ~2 °/s of wobble)
   restarts it, and the terminal prints `keep still during bias measurement; measuring bias again`.
   If that keeps happening, rest the band on a table for the 5 s, put it on, then face +x and press `z`.
   The panel shows `heading not ready` until calibration is done.
4. **Running:** the panel's yellow arrow follows the worker's head. 0° = facing +x; turning left
   (counter-clockwise seen from above) is positive.
5. **Re-zero** whenever the worker faces +x again (start of each run, or after drift): press `z` in the
   overlay window, or open `https://localhost:8443/zero`.

| Check | How | Pass |
|---|---|---|
| Sign | After calibrating, turn the head left | heading goes **up** (positive). If it goes down, restart with `--muse-flip` |
| Scale | Face +x, press `z`, then face +y (toward marker 3) | about +90° |
| Facing | Look straight at the car | relative angle near 0, **FACING**; look 90° away → **NOT FACING** |
| Drift | Stand still for a few minutes | heading moves slowly (well under a few °/min); re-zero with `z` |
| Dropout | Switch the band off | panel shows `muse: NO DATA` within 2 s and the verdict blanks; it reconnects by itself |

Options: `--muse-name MuseS-1A2B` / `--muse-mac` when several bands are around, `--muse-board muse_s_bled
--muse-port COM3` for the BLED112 dongle, `--muse-bias 5` calibration seconds, `--muse-flip` to negate
the yaw, `--facing-deg 30` the facing cone. Without hardware, `--muse synthetic:joint` (recorded fake head
motion) or `--muse --muse-board synthetic` (BrainFlow's fake board) run the same code path.

`a` / `d` still work with `--muse`: they add a fixed offset on top of the Muse heading (shown in the panel).

**Limits.** The Muse has no magnetometer, so the heading is relative to the last zero and drifts slowly.
`max/calibration.json` still holds placeholder values (axis, sign, scale) until Max's axis and scale
experiments are run on our band. `max/README.md` and `max/docs/` cover those experiments and the
standalone tools (`max/scripts/live_yaw.py`, `record.py`, `analyze.py`).

## What isn't testable yet

Velocities and time to contact, and blending the Muse heading with the camera (until then the Muse
heading drifts until re-zeroed).
