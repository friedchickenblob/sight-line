# SightLine — vision (detection + tracking)

Zoe's lane of the SightLine natHACKS 2026 project. Specs: `docs/plan-b.pdf` (team plan) and
`docs/sightline-zoe-research-guide.pdf` (this lane's guide). Use the `sightline-vision` agent
(`.claude/agents/sightline-vision.md`) for work in this folder.

Layout:
- `clips/` — test clips A–G (`A.mp4` … `G.mp4`), with camera height + lens noted in `clips/README.md`
- `scripts/` — capture, evaluation, speed and tracking scripts
- `results/` — filled-in grids and tracks CSVs

Setup: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
