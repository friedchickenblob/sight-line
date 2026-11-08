# Vision results: clips A–G (2026-11-07)

Tracks: `results/tracks_<clip>.csv`, columns `t,id,cls,x_m,y_m,vx,vy`. `t` is seconds from the start of the clip
(not `pylsl` clock time: the Muse wasn't recording). x/y are metres on the floor frame (2.25 × 1.5 m rig).
Machine id is always `20`, worker id is always `10` (one worker in view).

## How the tracker finds things

- **Machine (chair):** ArUco marker 20 when it can be read. When it can't, the tracker follows the big black blob
  near where the chair was last seen. Marker 20 is only about 20 px wide in these 1280×720 clips, so video
  compression often blurs its pattern beyond reading.
- **Worker:** hardhat marker 10 (95–99% of frames on C, D, F). When it can't be read, the YOLO person box
  nearest the worker's last position.

## Coverage and ID tests

| Clip | What's in it | Machine found | Worker found | IDs | Notes |
|------|--------------|---------------|--------------|-----|-------|
| A | truck alone | 98% | n/a | 1 machine ID, 0 switches | 1 s gap at 18.7 s while the chair was moved between runs |
| B | worker alone | n/a | 91% | 1 worker ID | crouching hides the hardhat marker (74% marker) |
| C | truck passes worker | 100% | 96% | 1 + 1 | camera bumped at 84 s; the floor map refits by itself |
| D | 5 approach runs | 99% | 100% | 1 + 1 | the main clip for scoring the alert rule |
| E | worker hides the truck | 71% | 74% | 1 + 1 | **machine lost 64–88 s while the worker covers it** (expected, see the clip log) |
| F | dim, then lamp | 70% | 98% | 1 + 1 | machine lost 43–48 s and 64–68 s, the darkest parts |
| G | false alarms, no truck | 0% (correct) | 18% | 1 worker ID | the "worker" is the non-worker walking in at 77–96 s: without a hardhat marker, any person in the rig counts |

The ID switches are counted by the script, not by eye yet.

## Tracking tests (`check_tracks.py`)

- **parked** (A, 57–62 s): median speed 0.012 m/s, PASS. Wobble 2.2 cm, FAIL by 0.2 cm against the 2 cm target.
- **ids** (A, C, D, E, F): 1 machine ID, 0 switches, PASS on all.
- **heading** (D, worker at (1.72, 0.62), `--min-speed 0.03`): run 1 14.9°, run 2 5.4°, run 3 3.2°, run 5 6.9°,
  all PASS (target within 15°). Run 4 is a pass-by at y ≈ 1.1 m, not an approach, so the heading test doesn't apply.
  The chair was pushed at about 0.05 m/s, so the default `--min-speed 0.1` counted almost no frames as moving.

## "Truck arrives" times, clip D (machine within 1 m of the worker, from the tracks)

| Run | Window (s) | Within 1 m at (s) | Closest | At (s) |
|-----|------------|-------------------|---------|--------|
| 1 | 0–20 | 9.4 | 0.47 m | 19.0 |
| 2 | 21–42 | 33.1 | 0.39 m | 41.9 |
| 3 | 43–60 | 50.2 | 0.52 m | 58.9 |
| 4 | 61–93 | 77.1 | 0.32 m | 87.0 |
| 5 | 94–105 | 95.6 | 0.39 m | 98.8 |

These come from the tracks. Check each one by eye in the video before scoring against them.

## Known problems

1. Marker 20 is too small for 1280×720. If anything gets refilmed: use the 1× lens at 1080p or 4K, and print
   marker 20 at about 12 cm.
2. When the worker stands over the chair (E, 64–88 s), the machine is lost. The alert should rely on the
   head-stale / machine-stale fail-safe there.
3. The blob fallback assumes the chair is the only big dark shape. A worker in dark clothes right next to it
   could merge into the same blob.
4. Any person in the rig counts as the worker when the hardhat marker isn't seen (clip G).
