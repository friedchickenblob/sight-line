# CLAUDE.md

Rules for working in this repo. SETUP.md has the full explanation for humans.

## The project

SightLine is a natHACKS 2026 project by Jordan, Hanh, Zoe, Sean and Max. An overhead camera tracks a worker and an RC "machine", a Muse S headband tracks which way the worker's head points, and an ESP32 wristband buzzes only when the machine is about to reach the worker **and** the worker hasn't looked at it. Each person's research guide (a PDF on Google Drive) and the reference docs are linked in the README's "Resources" section.

## Conventions every module shares

- **Floor frame:** metres, origin at floor marker 0, +x toward marker 1, +y toward marker 3.
- **Angles:** degrees (never radians in messages), 0° along +x, counter-clockwise positive seen from above, wrapped to [-180, 180).
- **Time:** `pylsl.local_clock()` seconds, taken when the data was captured.
- **Messages:** JSON over MQTT, topics under `sightline/`, field names in snake_case with unit suffixes (`_m`, `_s`, `_deg`, `_dps`), and `"v": 1` in every payload.

## Setup

In a fresh clone, run `make hook` (or `git config core.hooksPath .githooks`) before committing. This turns on the commit-message and pre-push hooks.

## Branches

- Never push to `main` and never commit directly on `main`. Create a branch, push it, and open a pull request.
- Never force-push to `main`. `git push --force-with-lease` is fine on your own branch.
- Branch names are `owner/what-it-implements`: a lowercase owner from `jordan`, `hanh`, `zoe`, `sean`, `max`, a slash, then kebab-case (lowercase letters, digits and hyphens only). Examples: `jordan/claude-rules`, `zoe/bytetrack-tracking`, `max/gyro-drift-test`.
- The owner is the teammate you're working for. If you don't know who that is, ask before creating the branch.

## Commit messages

Every commit on every branch must have a first line in this format:

```
type(scope): description
```

- `type` is one of: `docs`, `style`, `feat`, `fix`, `refactor`, `test`, `build`, `ci`, `chore`.
- `(scope)` is optional and can be anything, e.g. `(vision)`, `(wristband)`, `(fusion)`.
- Breaking changes put `!` right before the colon: `feat(api)!: rewrite auth logic`.
- Lowercase type, one space after the colon, then a short description.
- Pull request titles follow the same format, because squash-merges use the title as the commit message.

Examples:

```
feat(vision): add ByteTrack tracking to the camera loop
fix(wristband): turn off Wi-Fi sleep to cut buzz latency
docs: add research guides for each teammate
refactor(fusion): split the alert rule into its own module
test(fusion): cover the head-stale fail-safe
chore(deps): bump ultralytics to 8.4
feat(api)!: rewrite auth logic
```

## When running on GitHub (claude-code-action)

You were started by an `@claude` comment or the `claude-ok` label, and nobody is watching live. Work so the result can be reviewed in the morning.

- The action creates your branch with the `jordan/claude-` prefix. Keep that branch; don't create or rename branches.
- Do exactly what the issue or comment asks: one issue, one small pull request. Don't fix unrelated things you notice; mention them in your reply instead.
- Issues from the Task form have "Owner", "Done when" and "Needs hardware to test?" fields. Treat "Done when" as the acceptance test. If "Needs hardware" is Yes, do only what can be done without it and say what's left.
- Pull requests are squash-merged, so the PR title becomes the commit on `main`. Get the title right.
- If the request is unclear or has no way to tell when it's done, reply with your questions instead of guessing.
- Run the relevant tests or scripts before finishing. If something can't be checked without hardware (camera, Muse, wristband), say so plainly in your reply.

### Opening the pull request

When your commits are pushed, open the pull request yourself instead of only posting a "Create PR" link:

Use the `.pr/` folder at the repo root for these files. It's in `.gitignore`, so nothing in it can be committed. Don't write to `.git/` or `/tmp` (the action blocks both), and don't pass a multi-line `--body` on the command line (it's rejected too).

1. Read `.github/pull_request_template.md`. With your file-writing tool, create `.pr/body.md`, keeping every section of the template and filling each one in: what changed, `Closes #<issue>`, why, how you checked it (tick only what you actually did), whether it's a breaking change, and the checklist.
2. With your file-writing tool, create `.pr/title.txt` containing only the title, then check it:
   ```bash
   sh .githooks/check-commit-msg .pr/title.txt
   ```
   If it fails, fix the title and check again.
3. Create the pull request (not as a draft), with the same title:
   ```bash
   gh pr create --base main --title "docs(readme): example title" --body-file .pr/body.md
   ```
4. Put the pull request link in your reply on the issue. If `gh pr create` fails, say why and fall back to the "Create PR" link, with the filled-in template as its body.
- Don't touch firmware, hardware timing, calibration constants, `.githooks/` or `.github/workflows/` unless the issue explicitly asks.
- Never commit secrets, tokens, recordings or datasets.

## If a hook rejects something

- Fix the problem and retry. Never use `--no-verify` to get around a hook.
- A rejected commit message: reword it to match the format above and commit again.
- A rejected push to `main`: move the work to a branch (`git switch -c <owner>/<what-it-implements>`), push that, and open a pull request.
- A rejected branch name: rename it with `git branch -m <owner>/<what-it-implements>` and push again.
