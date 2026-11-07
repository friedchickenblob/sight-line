# Hanh: floor mapping and hardhat yaw

Stand-alone scripts for the overhead camera, using ArUco markers (DICT_4X4_50). Floor frame as in CLAUDE.md: metres, origin at marker 0, +x toward marker 1, +y toward marker 3. All scripts use 1280×720.

| Script | What it does |
|---|---|
| `floor_check.py` | Builds the floor map from markers 0–3 and tests it on held-out marker 4 over 20 frames. Prints PASS/FAIL and saves `floor_check.json`. |
| `floor_map.py` | Live floor map: shows each marker's floor position in metres, click to read any point, `s` saves `floor_calib.json`. |
| `head.py` | Hardhat marker 10: yaw in degrees and floor position. Face +x and press `c` once to calibrate. |
| `detect.py` | Shows every detected marker with its size in pixels, plus fps. |
| `make_print_pdf.py` | Writes `markers.pdf`, one marker per page, labelled with its role. |
| `make_markers.py` | Writes `marker_XX.png` files. |

## Marker IDs

| ID | Role |
|---|---|
| 0–3 | Floor corners |
| 4 | Floor check (held out) |
| 10, 11 | Hardhat, spare |
| 20, 21 | RC car, spare |
| 30–33 | Tool-return tags (stretch) |

## Results so far

Rig: 1.5 × 0.8 m floor, camera on the ceiling 2.5 m up, looking straight down, 1280×720.

**Floor mapping** (`floor_check.py`): all 5 markers found in 20/20 frames, held-out marker 4 off by 2.5 cm median and 2.6 cm worst (target ≤ 10 cm), jitter 0 mm. PASS.

**Hardhat yaw** (`head.py`, marker 10 on a worn hardhat, `OFFSET = 3.5`):

| Facing | Expected | Read | Error |
|---|---|---|---|
| 1–2 side (+x) | 0° | 0° | 0° (calibration point) |
| 2–3 side (+y) | +90° | +90° | 0° |
| 0–3 side | ±180° | +180° | 0° |
| 0–1 side | −90° | −93° | 3° |

- **Head tilt** (facing the 0–1 side): looking down reads −89° (about 1° shift), looking up reads −102° (about 12° shift). Past those tilts the marker is lost, which is where the gyro has to carry the reading.
- **Hidden marker:** still read with arms raised, leaning, reaching, and at the floor edges.

**Hardhat position:** with the marker on a 7 cm box over marker 4 (1.00, 0.30) and `MARKER_H_M = 0.08`, it read (1.03, 0.25), about 6 cm off. `NADIR` is now measured with a plumb line at (0.85, 0.60). Position on a worn hardhat is not measured yet.
