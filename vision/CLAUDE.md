# SightLine — vision (detection + tracking)

Zoe's lane of the SightLine natHACKS 2026 project. Specs: the project plan and Zoe's and Hanh's research guides,
linked in the root README's "Resources" section (PDFs on Google Drive, not in the repo). Use the `sightline-vision`
agent (`.claude/agents/sightline-vision.md`) for work in this folder. Next steps and the clip recording script are in
`RECORDING-PLAN.md`.

Layout:
- `clips/` — test clips A–G (`A.mp4` … `G.mp4`, git-ignored, kept on the shared drive), the clip log in
  `clips/README.md`, and `clips/floor.json` with the measured floor-marker positions (copy `floor.example.json`)
- `scripts/` — `prep_clip.py` (convert + check phone clips), `eval_grid.py` (detection grid), `speed_test.py`,
  `track.py` (tracking → CSV), `floor_map.py` (temporary floor map until Hanh's `geometry.py`), `check_tracks.py`
  (tracking tests), `record_clips.py` (record from a webcam)
- `results/` — filled-in grids and tracks CSVs

Run scripts from `vision/` (they use relative paths like `clips/A.mp4`).

Setup: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
