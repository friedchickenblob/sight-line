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

Floor check on a 1.5 × 0.8 m floor, camera on the ceiling 2.5 m up: all 5 markers found in 20/20 frames, held-out error 2.5 cm median and 2.6 cm worst (target ≤ 10 cm), jitter 0 mm. Hardhat yaw accuracy is not measured yet.
