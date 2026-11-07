# Learning notes: agentic coding with pstack

This branch was built by an AI agent (Claude) following **pstack**, a skill pack of
engineering workflows, so you can see what agent best practice looks like on a real
task. These notes cover what pstack is, how to install it yourself, and which parts of
it changed decisions here.

## What pstack is

pstack started as Cursor's plugin of "how to do engineering work" instructions.
**uni-pstack** (github.com/kashyab12/uni-pstack) ports it to Claude Code and Codex as
*skills*: markdown files (`SKILL.md`) that an agent loads when a task matches. It has:

- an entry skill `pstack` that says: open a todo list, read the principles, pick a
  **playbook** (Feature, Bug fix, Refactor, Investigation, ...) and copy its steps in;
- subskills: `architect`, `arena` (try 2–3 designs, compare), `interrogate`
  (adversarial review), `tdd`, `show-me-your-work` (decision log), `how`/`why`
  (explain code), `unslop` (plain writing), `reflect`, `recall`, ...;
- ~20 `principle-*` skills (Laziness Protocol, Prove It Works, Boundary Discipline, ...).

One quirk: in Claude Code, uni-pstack wants **subagents to run on OpenAI's Codex CLI**
(`spawn-codex-worker.sh`), so a second model family does the delegated work and
reviews. Codex wasn't available in this session, so delegated steps used Claude agents
instead. That's logged in `decisions.tsv`.

## Install it yourself

Not run for you, as asked. On Windows, in **Git Bash** (the installer is a bash script):

```bash
git clone https://github.com/kashyab12/uni-pstack.git ~/uni-pstack
cd ~/uni-pstack
./install.sh --claude --dry-run     # see what it will write to ~/.claude/skills
./install.sh --claude               # install for all your Claude Code projects
```
Or per-project (commits the skills into the repo; probably not what the team wants):
`./install.sh --claude --claude-dir .claude/skills`. There is also
`npx skills add kashyab12/uni-pstack`. Restart Claude Code, then try
`/pstack implement the reference-glance reset`.

For the Codex delegation to work you'd also need the Codex CLI and an OpenAI account.
Without it, just tell the agent "use Claude subagents instead of Codex".

## How it shaped this branch

| pstack idea | What it did here |
|---|---|
| **Name the data shape first** | Before any algorithm: `Sample(kind, t, v)`, one `Source` interface (Muse / replay / synthetic), the three message dataclasses, `Calibration` with a status per value. Replay then came for free (p.7c). |
| **Prove it works / build the lever** | No hardware, so the first real deliverable was a *simulator* (`synth.py`) that can produce every experiment in the guide with a known right answer. Every algorithm is checked against ground truth. |
| **Sequence verifiable units** | 79 tests, each tied to a guide "check" or "DONE WHEN" line. Commits go shapes → simulator → yaw → contact → analysis → scripts → docs. |
| **Fix root causes** | 8 tests failed on the first full run. Diagnosing them (not loosening thresholds) found the lagging gravity filter, the clipped-turn recovery gap, and five test bugs where *my test setup* was unrealistic. See the next section. |
| **Boundary discipline** | Validation happens where data enters: `measure_bias` refuses if you moved, LSL type names are resolved in one place, CSV parsing in one place. Internal maths trusts its inputs. |
| **Never block on the human** | Waited on you only for things that were yours: which pstack, cloning with your SSH key. Everything else proceeded and was logged. |
| **Show me your work** | `decisions.tsv`: one row per decision, with evidence. |
| **Interrogate** | An independent reviewer agent wrote scratch experiments to break the code. It found 10 issues: 7 fixed with regression tests, 1 deferred as a strict xfail test, 1 needs hardware, 1 I'd already fixed. All logged in `decisions.tsv`. |

## The most useful lesson: when a test fails, decide who's wrong

Of the 8 failures on the first run:

- **3 were real algorithm problems.** The accelerometer filter lagged nods; K=0.05
  couldn't repair a clipped turn in 1 s; camera misreads wrecked the lag estimate. All fixed in code.
- **5 were bad tests.** A bias that drifts 3 °/s every minute (absurd); a "return to
  90°" that spun 300° in 1.5 s and clipped; glance durations that ignored time spent
  turning through the cone. All fixed in the tests, *without* loosening any threshold.

An agent (or a tired human) under pressure is tempted to make red tests green by
weakening them. Don't. Ask "is the code wrong, or is the test's world wrong?", and
write down the answer.

## What the review caught that 79 green tests didn't

The independent reviewer's most valuable finding: **my simulator applied the same axis
convention to the gyro and the accelerometer**, so a mirrored gyro (which BlueMuse says
the Athena has) could never show up in tests. Both sides of the test shared an
assumption. Its other big catch was a gate that "remembered" a misread forever.
Green tests only prove what they test; a reviewer who didn't write them asks
different questions.

## Practices worth stealing for your own agent work

1. **Give the agent the spec, not just the goal.** The research guide's tables and
   "DONE WHEN" lines became tests almost one-for-one.
2. **Make "not done yet" explicit in the artifact.** `calibration.json` marks every
   unmeasured constant as `PLACEHOLDER`; `live_yaw.py` prints them on start. Hardware-
   pending work is labelled ⏳ in the docs, not silently faked.
3. **Fake data with a known answer beats no data.** But make the fake honest: the
   simulator includes neck-lever acceleration, clipping, dropped packets and misreads.
   Without the lever, the guide's simpler filter would have looked fine.
4. **Check facts at the source.** Reading the muselsl and BlueMuse code found that
   BlueMuse names its streams differently (the guide's code would find nothing) and that
   the gyro clips at 245 °/s.
5. **Small commits in dependency order**, each one explainable in a sentence.
6. **Keep a decision log.** Future-you on Thursday will not remember why K jumps to 0.2.
