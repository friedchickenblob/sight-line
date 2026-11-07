# Vision: next steps and recording plan

This is the plan for recording test clips A–G with a toy truck and a phone, then running the first
experiments on them. Every experiment in the vision lane runs on these same clips, so the numbers can be
compared. Record everything in one session, with the camera fixed in place the whole time.

The truck clips (A, C, D, E, F) are **practice clips** until the real machine and camera arrive. Then
re-record A, C and D. Clips B and G don't need the truck, so they can be final now.

---

## Part 1. What Hanh found, and what it means for the clips

| Hanh's finding | What it means here |
|---|---|
| The webcam's field of view is only ~49° | At 3.5 m high it sees only ~3.1 × 1.7 m of floor, so a 3 × 2 m rig doesn't fit. Use the phone or a wider camera, and size the rig to what the camera actually sees. |
| Car marker (7 cm) is ~29 px at 3.4 m: marginal | Make the marker on the truck as big as its roof allows (10–12 cm if possible). |
| Hardhat marker (9 cm) is ~76 px: comfortable | Wear the hardhat with marker 10 in every clip with a worker. |
| Head zone at 3.5 m is only ~1.5 × 0.85 m | Clips A–G are about detecting the truck and the person, so this doesn't block you. The worker's standing spot in clip D should be near the centre of the image. |
| Recommended for Sean: a ~70° camera at ~3.5 m, giving a 3 × 2 m rig | These clips are practice until that camera is mounted. |

---

## Part 2. Things to have ready

- [ ] The phone, ideally **the same phone Hanh used**, mounted in the same place, so Hanh's floor map fits your clips too
- [ ] A way to mount the phone **high and pointing straight down**: a tripod with a boom arm, a shelf, or taped to the ceiling. Higher is better.
- [ ] The toy truck, **at least 15–20 cm long** (bigger is easier to detect from high up)
- [ ] A **thin, dark string about 4 m long**, tied low on the front of the truck. A second string on the back lets you
      pull it back without touching it. (If you have a remote-control truck, use that instead and skip the strings.)
- [ ] Floor markers **0, 1, 2, 3** (from Hanh), marker **20** on the truck's roof, marker **10** on the hardhat
- [ ] Masking tape and a tape measure
- [ ] A helper (the "worker" or the truck driver), a cardboard box, a backpack or bag, a lamp
- [ ] A matte, plain floor if possible. Shiny floors reflect light and make markers disappear.
- [ ] This page printed or open, and the **clip log** (Part 6) to fill in as you go

---

## Part 3. Setting up the floor and the camera

### 3.1 The floor frame (same for the whole team)

```
   y
   ▲
 [3] ─────────────── [2]
  │                   │
  │      (rig)        │
  │                   │
 [0] ─────────────── [1] ──► x
(0,0)
```

- Origin at the **centre of marker 0**. **+x** from marker 0 toward marker 1, **+y** from marker 0 toward marker 3.
- Seen from above, the markers go 0 → 1 → 2 → 3 **counter-clockwise**. If they don't, the map comes out mirrored.
- Put the markers **just outside** the taped rig, so the truck never drives over them.

### 3.2 Step by step

1. **Mount the phone** as high as you can, pointing straight down at the middle of the floor.
2. **Hold the phone in landscape** and turn it until, **on the screen**, marker 0 is at the bottom left, 1 at the bottom right,
   2 at the top right and 3 at the top left. Then +x is "right" on screen and +y is "up".
3. **Size the rig to the camera.** Aim for 3 × 2 m (the plan). If the camera can't see that much, make it smaller.
   All four floor markers must be fully in the picture with a little space around them.
4. **Tape the rig outline.** Measure each floor marker's **centre** with the tape measure and write the positions in the clip log.
5. **Tape the spots used in the scripts** (Part 4) for a 3 × 2 m rig. Scale them if the rig is smaller.

   | Spot | Position (m) | Used for |
   |---|---|---|
   | **W** (worker spot) | (2.0, 1.0) | Where the worker stands in C and D. Mark it with an X. |
   | **S1** | (0.3, 1.0) | Truck start: straight along +x toward W |
   | **S2** | (0.3, 0.3) | Truck start: diagonal toward W |
   | **S3** | (0.3, 1.7) | Truck start: other diagonal toward W |
   | **L1, L2** | Lines across the rig at x = 0.5 and x = 2.5 | The 2 m timed run in clip A |
   | **P** (park spot) | Centre, (1.5, 1.0) | Where the truck is parked in A and E |

6. **Measure the camera height** (lens to floor) and find the **point straight under the lens**: hang a weighted string from
   the phone and mark where it touches the floor. Write both in the log. Hanh's parallax fix needs them.
7. **Phone camera settings:**
   - Video **1080p at 30 fps**, **1×** lens (write down the lens you used)
   - **Stabilisation off**, **HDR off**, no Cinematic or Action mode
   - **Lock focus and exposure** (iPhone: long-press on the floor until it says AE/AF LOCK)
   - iPhone: Settings → Camera → Formats → **Most Compatible**
8. **Test recording, 10 s:** walk across the rig with the hardhat on, then watch it back. Check that all four floor markers are
   sharp and nothing is cut off at the edges.
9. **From now on, don't touch the phone or move the markers.** If anything moves, write it in the log.

---

## Part 4. Script for each clip

General rules:

- Stop and start a new video for each clip. Write down the phone's file name (e.g. `IMG_1234`) next to the clip letter
  in the log right away.
- Keep the truck driver's hands and body **out of the picture**: stand outside the rig and pull the string, or drive by remote.
- **Pulling with a string:** pull slowly and steadily, low and level with the floor, so the truck doesn't tip or jerk.
  To change direction, walk around the outside of the rig and pull from the new side.
- Wait **3 seconds without moving** at the start and end of each clip, and between runs.
- If a take goes wrong, record it again and write "take 2" in the log.

### Clip A: truck alone (~90 s)
**What it tests:** whether the truck is detected everywhere, including at the image edges; whether the parked truck "wobbles"; whether the measured speed is right.

1. Truck parked at **P**, nobody in the picture: **10 s still**.
2. **Timed 2 m run:** put the truck before line L1, then drive it in a straight line along y = 1.0 at a steady speed across
   L1 and L2, all the way through.
3. Move the truck through **all four corners** and along **all four edges**, plus both diagonals. With a string, pull from a
   different side each time so the truck **faces every direction** at some point (+x, −x, +y, −y, and diagonals).
   Stop for 2 s in each corner. With a remote, add loops and turns.
4. Park in one corner: **10 s still**.

### Clip B: worker alone, no truck (~60 s)
**What it tests:** whether YOLO detects a person seen from straight above.

1. Walk around the edges of the rig, then along both diagonals.
2. Stand still at the centre for **5 s**.
3. Crouch for **5 s**, then stand up.
4. Reach up with both arms, as if taking something from a shelf.
5. Pick up the box, carry it across the rig, and put it down.
6. Turn slowly in place, a full circle.

### Clip C: truck passing the worker (~90 s)
**What it tests:** detecting both at once, in the normal demo scene.

1. Worker stands at **W**, hardhat on, and stays roughly in place, turning their head now and then.
2. The truck passes the worker in straight lines along y (across the rig), the puller standing outside the rig at the
   +y or −y edge. Each line is pulled once in each direction:

   | Pass | Line | Distance from W |
   |---|---|---|
   | 1–2 | x = 0.5 | 1.5 m |
   | 3–4 | x = 1.0 | 1.0 m |
   | 5–6 | x = 1.5 | 0.5 m |

3. With a remote, finish with the truck circling the worker once at about 1 m. With a string, skip this.

### Clip D: truck straight at the worker, 5 runs (~90 s)
**What it tests:** the scene the alert is made for. Afterwards we check the velocity points at the worker (within 15°).

In every run the worker stands at **W**. **The truck stops about 30 cm before the worker's feet.** Don't hit anyone.

With a string: the puller stands outside the rig **beyond the worker**, on the line from the start spot through W, and
the worker stands with the string **between their feet**. Then the truck heads straight at the worker.

| Run | Start | Worker faces | Speed |
|---|---|---|---|
| 1 | S1 | +x (back to the truck) | slow |
| 2 | S1 | −x (looking at the truck) | slow |
| 3 | S2 | +x | slow |
| 4 | S3 | +x | slow |
| 5 | S1 | +x | as fast as safely possible |

Between runs: bring the truck back to its next start spot, then wait 3 s before starting. Write each run's start and end
times (seconds into the video) in the log after recording.

### Clip E: worker next to the truck (~60 s)
**What it tests:** the worker blocking the truck from view, and whether the truck keeps one ID the whole time.

1. Truck parked at **P**.
2. The worker walks up to it and stands right next to it for 5 s.
3. Steps over it and stands on the other side for 5 s.
4. Crouches beside it, partly covering it from the camera, for 5 s.
5. Walks away. Then the truck drives off to a corner.

### Clip F: clip C again in bad lighting (~2 × 45 s)
**What it tests:** whether detection holds up when the light changes.

1. **Lights dimmed:** do the clip C script, shortened to about 45 s.
2. **One bright lamp** from the side, aimed so it causes glare: do the clip C script again, about 45 s.

Record both halves in one video.

### Clip G: false alarms, no truck (~60 s)
**What it tests:** things that aren't the truck being detected as the truck. Any truck box in G is a false alarm.

1. Empty rig: **15 s**.
2. Put down a backpack, the box and a shoe. Leave them: **10 s**.
3. A second person (no hardhat) walks through the rig, stops for a moment, and walks out.
4. Push a chair or another object with wheels across the rig, if you have one.

### Optional: extra footage for fine-tuning (~3 min)
If YOLO turns out to miss the truck often, we'll need images to train on. Drive the truck around in many positions
and directions, with and without the worker nearby. Record a different floor or different lighting if you can.

---

## Part 5. After recording: your steps

1. **Copy the videos to the Mac** (AirDrop) into a folder outside the repo, e.g. `~/sightline-raw/`.
2. **Install ffmpeg** once: `brew install ffmpeg` (the script below falls back to OpenCV without it, but ffmpeg is better)
3. **Convert and check each clip.** This converts it to a steady 30 fps (phones record a varying frame rate), names it by
   its letter, and checks it: resolution, length, all four floor markers visible, and the marker sizes in pixels.
   ```bash
   cd ~/sight-line-prep/vision
   python scripts/prep_clip.py ~/sightline-raw/IMG_1234.MOV A      # -> clips/A.mp4
   ```
   If it prints `PROBLEM:`, fix it before moving on (e.g. re-record if a floor marker is never seen).
   `clips/*.mp4` is git-ignored, so the videos stay out of the repo.
4. **Upload** the raw and converted clips and the filled-in clip log to the **shared drive**, and send Hanh the link.
5. **Set up the Python environment** if you haven't yet:
   ```bash
   cd ~/sight-line-prep/vision
   python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
   ```
6. **What does YOLO call the truck?**
   ```bash
   python scripts/eval_grid.py names A
   ```
   It lists how many frames each class appears in. The truck is probably "truck" or "car".
7. **Detection rate grid**, using that name:
   ```bash
   python scripts/eval_grid.py grid --car truck        # writes results/grid.md
   ```
   Then apply the decision rule from the guide:
   - The truck is found in **95% or more** of frames on A and D: the pretrained model is enough.
   - **70–95%:** try prompting the open-vocabulary model, then fine-tune.
   - **Below 70%:** fine-tune, and ask Hanh to make the truck marker the main input for now.

   Apply the same rule to the person on clip B.
8. **Look at the results with your own eyes:**
   ```bash
   yolo predict model=yolo26n.pt source=clips/D.mp4 save=True      # annotated video in runs/detect/
   ```
9. **Tracking.** First copy `clips/floor.example.json` to `clips/floor.json` and fill in the **measured** floor-marker
   centres, camera height and point under the camera from the clip log. `track.py` uses them to turn pixels into metres,
   fitting the map from the markers it sees in the clip, until Hanh's `geometry.py` replaces this.
   ```bash
   python scripts/track.py clips/A.mp4 results/tracks_A.csv
   python scripts/track.py clips/D.mp4 results/tracks_D.csv
   ```
10. **Bring to the check-in:** `results/grid.md`, the decision, and a short clip of D with the boxes drawn on.

---

## Part 6. Clip log (fill in while recording)

Save it as `clips/README.md` and upload it to the shared drive with the clips.

```
Date:                       Location:
Phone model:                Lens: 1× / 0.5×        Resolution / fps:
Camera height (lens to floor, m):
Point under the camera (x, y, m):
Rig size (m):               Visible floor in the image (m × m):
Floor marker centres (measured, m):  0: (   ,   )  1: (   ,   )  2: (   ,   )  3: (   ,   )
Marker sizes (measured black square, cm):  floor:     hardhat (10):     truck (20):
Truck: model / size:        Driven by: remote / string
Worker height and hardhat marker height (m):

| Clip | Phone file | Take | Duration | Notes (runs, times, what went wrong) |
|------|------------|------|----------|--------------------------------------|
| A    |            |      |          | 2 m run: crossed L1 at __ s, L2 at __ s |
| B    |            |      |          |                                      |
| C    |            |      |          |                                      |
| D    |            |      |          | run 1: __–__ s, run 2: ..., run 5: ... |
| E    |            |      |          |                                      |
| F    |            |      |          | dim: __–__ s, lamp: __–__ s          |
| G    |            |      |          |                                      |
```

---

## Part 7. Code work in parallel (Claude, no hardware needed)

Done on the `zoe/...` branch, each change as a small commit, so the scripts are ready when the clips are:

1. **`track.py`: use the video's own time.** Time each frame by frame number ÷ fps for video files, and keep
   `local_clock()` for the live camera. Without this, every velocity from a clip is wrong.
2. **`scripts/prep_clip.py`:** convert a phone video to 30 fps and check it: resolution, fps, length, all four floor markers
   detected, and the truck marker's size in pixels.
3. **A temporary floor map from the clip itself:** fit the pixel-to-metre map from markers 0–3 in the first frame, using the
   measured positions from the clip log. This keeps tracking in real metres until Hanh's `geometry.py` and
   `floor_calib.json` arrive, and is swapped out for them as soon as they do.
4. **`eval_grid.py`:** choose the truck's class from the command line, read the fps from the video instead of
   assuming 30, and skip missing clips with a message instead of crashing.
5. **Tracking checks** (`scripts/check_tracks.py`): parked wobble and speed (target < 2 cm and < 0.05 m/s), the 2 m run's speed
   against the time between L1 and L2 (within 10%), one truck ID on E, and on D the velocity direction against the
   direction to W (within 15°).
6. **Worker parallax fix** in `track.py` using Hanh's formula (h ≈ 1.3 m for the body box), with the camera height and the
   point under the camera from the clip log.
7. **Bring the notes up to date:** rig 3 × 2 m and Hanh's camera numbers in the vision agent file; draft output column names
   that follow the team rules (snake_case with units, e.g. `vx_mps`) for Jordan to approve.

None of this needs the camera, but none of it can be tested on real data until the clips exist. The clips are
what everything else waits for.
