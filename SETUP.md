# Setup and contributing guide

How to set up your laptop, work on the repo and use Claude. For what the project is, see the [README](README.md).

## Resources

### Hackathon

- [natHACKS 2026 Hacker Handbook](https://drive.google.com/file/d/1xQALUJ5_ZSfrrZyot5W1Nc1-5Ie-RXsS/) (`nh26 Hacker Handbook.pdf`)

### References

| What | Link | Used for |
|---|---|---|
| YOLO26 (Ultralytics) | [docs.ultralytics.com/models/yolo26](https://docs.ultralytics.com/models/yolo26) | Detecting the worker and the machine |
| Muse LSL | [github.com/alexandrebarachant/muse-lsl](https://github.com/alexandrebarachant/muse-lsl) | Streaming the Muse S gyro and EEG into Python |
| SH17 dataset | [keaipublishing.com news page](https://www.keaipublishing.com/en/news/sh17-a-dataset-for-human-safety-and-personal-protective-equipment-detection-in-manufacturing-industry/) | Real factory footage of people and PPE |
| OpenConstruction dataset survey (covers MOCS) | [Google Drive](https://drive.google.com/file/d/1B3jLSMNxWHQJXdcHUngA6XiOkbNOdYLX/) | Construction-site datasets, incl. MOCS |
| Roboflow ForkliftProject | [universe.roboflow.com/models-8ewny/forkliftproject](https://universe.roboflow.com/models-8ewny/forkliftproject) | A forklift detector to start from |

## Setup

### 1. Install the tools

You need **Git** and **make**. **Claude Code** and the **GitHub CLI** (`gh`) are optional but useful.

<details open>
<summary><b>macOS</b></summary>

```bash
xcode-select --install                               # Git and make
brew install gh                                      # GitHub CLI (needs Homebrew: brew.sh)
curl -fsSL https://claude.ai/install.sh | bash       # Claude Code
```
</details>

<details>
<summary><b>Windows</b> (PowerShell)</summary>

```powershell
winget install --id Git.Git                          # Git, including Git Bash
winget install ezwinports.make                       # make
winget install --id GitHub.cli                       # GitHub CLI
winget install Anthropic.ClaudeCode                  # Claude Code
```

Open a new terminal afterwards so the new commands are on your PATH. Run git and `make` from Git Bash or PowerShell; the hooks run through Git Bash either way.
</details>

<details>
<summary><b>WSL</b> (Ubuntu on Windows)</summary>

```bash
sudo apt update && sudo apt install -y git make gh   # if gh isn't found, follow cli.github.com/manual/installation
curl -fsSL https://claude.ai/install.sh | bash       # Claude Code
```

- Clone inside the Linux file system (e.g. `~/code`), not under `/mnt/c`: it's much faster and avoids line-ending problems.
- WSL has its own SSH keys and git config, separate from Windows. Set both up inside WSL.
- **Hardware code should run on Windows, not WSL.** WSL can't see Bluetooth (the Muse) and doesn't see USB webcams without extra setup (`usbipd`). WSL is fine for editing, git, Claude Code and anything that only talks to MQTT.
</details>

### 2. Connect to GitHub

```bash
gh auth login          # pick GitHub.com, SSH, and let it upload a key (or add an SSH key by hand)
git config --global user.name  "Your Name"
git config --global user.email "you@example.com"
```

If `gh` misbehaves:

- **"gh is not recognized" on Windows:** open a **new** terminal after installing it. Windows that were already open don't see the new PATH.
- **The "Authorize github" button won't click:** wait a few seconds and click once on the page first (GitHub disables it briefly on purpose), or pause ad blockers.
- **Signing in with a token instead:** use a **classic** token with the `repo`, `read:org` and `workflow` scopes. Fine-grained tokens (`github_pat_...`) only see the account they were made for, so `gh` can't find `ChickDream/sight-line-prep` and says "Could not resolve to a Repository".
- **`gh auth login --with-token` looks stuck:** it's waiting for input. Paste the token, press Enter, then Ctrl+Z and Enter (Windows) or Ctrl+D (macOS / Linux).

### 3. Clone and turn on the hooks (once per clone)

```bash
git clone git@github.com:ChickDream/sight-line-prep.git
cd sight-line-prep
make hook
```

`make hook` turns on the shared git hooks. They check your commit messages and branch names, and stop you from pushing to `main` by accident. Without `make`, run the same thing directly: `git config core.hooksPath .githooks`.

### 4. Python environment (once per clone)

All the Python code shares one requirements file at the repo root. From the repo root:

```bash
python -m venv .venv
.venv\Scripts\activate              # macOS / Linux / WSL: source .venv/bin/activate
pip install -r requirements.txt
python -m pytest                    # runs every folder's tests
```

`ultralytics` pulls in PyTorch, so the first install is a large download.

## Contributing

A short summary also lives in [`CONTRIBUTING.md`](CONTRIBUTING.md).

### No pushes to `main`

All work goes on a branch and reaches `main` through a pull request:

```bash
git switch -c zoe/camera-loop
# ...commit your work...
git push -u origin zoe/camera-loop
```

Then open a pull request on GitHub. The description is pre-filled from [`.github/pull_request_template.md`](.github/pull_request_template.md): fill in each section, and give the pull request a title in the [commit format](#commit-message-format) (e.g. `feat(vision): add ByteTrack tracking`), because a squash-merge uses the title as the commit message.

What enforces working through pull requests:

| Where | What it does | Status |
|---|---|---|
| `.githooks/pre-push` | Refuses `git push` to `main` on your machine | Active once you run `make hook` |
| `.github/workflows/protect-main.yml` | Fails loudly if a commit lands on `main` without a merged pull request | Active, but only reports: an Action runs *after* the push |
| **Protect main** ruleset (repo settings) | GitHub rejects direct pushes to `main`; changes need a pull request | **Not available yet** (see below) |
| [`.github/CODEOWNERS`](.github/CODEOWNERS) | Makes Jordan the owner of every file, so pull requests need Jordan's approval | **Not enforced yet**: it needs the ruleset |

> **Why the ruleset isn't on:** this repo is private and ChickDream is on GitHub's free plan. GitHub only enforces rulesets and branch protection on **public** repos, or on private repos with a paid plan (GitHub Team for organizations). Until one of those changes, nothing on GitHub *stops* a direct push or an unapproved merge. The hooks and the Protect main action are what we rely on, so **everyone must run `make hook`**, and please don't merge your own pull requests: ask Jordan.

Once the repo is public or on a paid plan, set the ruleset up (admin only): **Settings → Rules → Rulesets → New branch ruleset**, name it "Protect main", set enforcement to **Active**, target the default branch, and tick **Require a pull request before merging** (with **Require review from Code Owners** and 1 required approval) and **Block force pushes**.

### Branch names

Every branch except `main` is named after its owner, a slash, then what it implements in kebab-case:

```
owner/what-it-implements
```

- `owner` is one of `jordan`, `hanh`, `zoe`, `sean`, `max`, in lowercase.
- `what-it-implements` is lowercase words joined by hyphens: letters, digits and `-` only, no spaces, underscores or capitals.

```
jordan/claude-rules
zoe/bytetrack-tracking
hanh/floor-homography
max/gyro-drift-test
sean/wristband-firmware
```

Wrong: `Jordan/claude-rules` (capital), `jordan/claude_rules` (underscore), `jordan/ClaudeRules` (not kebab-case), `claude-rules` (no owner), `bob/claude-rules` (not on the team).

Checked in three places: the `post-checkout` hook warns as soon as you switch to a badly named branch, the `pre-push` hook refuses to push it, and the **Branch name** action fails on GitHub. Rename a branch with `git branch -m <owner>/<what-it-implements>`.

### Commit message format

Every commit on every branch must look like this. The scope in brackets is optional and can be anything.

```
type(scope): description
```

| Type | Use it for |
|---|---|
| `docs` | Documentation only |
| `style` | Formatting, whitespace, no code change |
| `feat` | A new feature |
| `fix` | A bug fix |
| `refactor` | A code change that neither fixes a bug nor adds a feature |
| `test` | Adding or fixing tests |
| `build` | Build system or dependencies |
| `ci` | CI configuration (GitHub Actions) |
| `chore` | Anything else: tooling, cleanup, config |

**Breaking changes** get a `!` right before the colon:

```
feat(api)!: rewrite auth logic
```

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

Rules: lowercase type, optional `(scope)`, optional `!`, then a colon, one space, and a description. Merge commits that git writes itself (`Merge branch ...`) are allowed.

### How the format is checked

- **On your machine:** the `commit-msg` hook rejects a bad message and shows the format with examples. Your message is kept, so fix it with `git commit -e -F .git/COMMIT_EDITMSG`.
- **On GitHub:** the **Commit format** action checks every new commit on every branch, plus pull request titles (a squash-merge uses the title as the commit message).

To fix a message that's already committed:

```bash
git commit --amend            # the latest commit
git rebase -i main            # older commits: mark them "reword"
git push --force-with-lease   # only on your own branch, never on main
```

### Issues

Open work as an issue with **New issue → Task** (or **Bug**). The form asks for the owner, the area, what to do, a "Done when" you can check, and whether it needs hardware. Title it like a commit message, e.g. `feat(vision): add ByteTrack tracking`. A well-filled Task is exactly what Claude needs to work on it overnight: add the `claude-ok` label (only for tasks that don't need hardware).

### Merging

- Pull requests are **squash-merged**: all the commits in a PR become one commit on `main`, and its message is the **PR title**. That's why PR titles must follow the commit format.
- Merge commits and rebase-merging are turned off, so `main` stays a clean list of PR titles.
- The branch is **deleted automatically** after merging. Start new work from an up-to-date `main`:
  ```bash
  git switch main && git pull
  git switch -c <owner>/<what-it-implements>
  ```

### What git ignores

[`.gitignore`](.gitignore) keeps virtual environments, caches, secrets (`.env`), recordings (`*.jsonl`, `*.xdf`, video), datasets (`data/`, `datasets/`, `recordings/`) and model weights (`*.pt`, `*.onnx`, `runs/`) out of git. Put those on the shared drive and link them from the issue or PR instead.

## Claude

[`CLAUDE.md`](CLAUDE.md) gives Claude the same rules as this guide (branch names, commit format, no pushes to `main`, never bypass the hooks) plus the project's shared conventions. Claude Code on your laptop and Claude on GitHub both read it automatically. If you change a rule here, change it there too.

### Claude on GitHub (works while you sleep)

Claude runs on GitHub's servers, so your laptop can be off. Two workflows:

| Workflow | When it runs | What it does |
|---|---|---|
| [`claude.yml`](.github/workflows/claude.yml) | Someone with write access comments `@claude ...` on an issue or PR, or adds the `claude-ok` label to an issue | Works on the issue on a `jordan/claude-...` branch, opens a pull request filled in from the template, and replies on the issue with the link |
| [`claude-standup.yml`](.github/workflows/claude-standup.yml) | Every night at 11 pm Alberta time (06:00 UTC), or by hand from the Actions tab | Posts a short standup to the "Standup log" issue: done, in review, blocked, overdue. Read-only apart from that comment |

**One-time setup (repo admin):**

1. Install the Claude GitHub app on `ChickDream/sight-line-prep`: [github.com/apps/claude](https://github.com/apps/claude).
2. Make a token from your Claude Pro/Max subscription:
   ```bash
   claude setup-token
   ```
   Add it under **Settings → Secrets and variables → Actions → New repository secret**, named `CLAUDE_CODE_OAUTH_TOKEN`. (To bill an Anthropic API account instead, add `ANTHROPIC_API_KEY` and swap the `claude_code_oauth_token` line in both workflows for `anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}`.)
3. Create the labels:
   ```bash
   gh label create claude-ok --color 7057ff --description "Claude may work on this"
   gh label create blocked   --color d73a4a --description "Waiting on something"
   gh label create standup   --color 0e8a16 --description "Nightly standup log"
   ```
4. Test it: **Actions → Claude nightly standup → Run workflow**, then check the "Standup log" issue. Then open a small test issue and add `claude-ok`.

The quicker alternative to steps 1–2 is running `/install-github-app` inside an interactive `claude` session in this repo. It installs the app and adds the secret, but writes its own `claude.yml`, so keep ours (it has the branch prefix and limits).

**The overnight routine:**

1. Before bed, write issues with a clear "done when" (one task per issue) and add `claude-ok`.
2. Claude works on each one, pushes a `jordan/claude-...` branch and opens a pull request.
3. In the morning, read the pull requests, ask for changes with `@claude ...` in a review comment, and Jordan merges what's good.

Pull requests that Claude opens show up as opened by **claude** (the Claude GitHub app), and the usual **Commit format** and **Branch name** checks run on them, including the PR-title check. Claude also checks its own title before opening the pull request. The workflow keeps **Settings → Actions → General → "Allow GitHub Actions to create and approve pull requests"** turned on as a fallback.

Claude's reply on the issue starts with a header the action writes itself, which still includes a generic **Create PR ➔** link. Ignore it when Claude says it opened the pull request: use the pull request link further down in the reply.

**Good tasks:** scripts, tests, schemas, fake data publishers, docs, small refactors. **Keep for humans:** firmware, hardware timing, calibration maths, anything needing the camera, Muse or wristband to test.

**Limits:** each run stops after 30 turns or 30 minutes (standup: 15 turns, 15 minutes), so a stuck task can't burn through usage overnight. Runs use the subscription or API account behind the secret, plus GitHub Actions minutes, which are limited on private repos. Check natHACKS' rules on AI coding tools before relying on this during the event.
