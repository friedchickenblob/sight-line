<!--
TITLE: use the commit format. A squash-merge turns the title into the commit message,
and the "Commit format" check fails the PR if it doesn't match.

  type(scope): description          types: docs style feat fix refactor test build ci chore
  type(scope)!: description         <- "!" for a breaking change

  feat(vision): add ByteTrack tracking to the camera loop
  fix(wristband): turn off Wi-Fi sleep to cut buzz latency
  feat(api)!: rewrite auth logic
-->

## What

<!-- One or two sentences: what this changes. -->

Closes #

## Why

<!-- The problem or issue it solves. Link guide pages or decisions if relevant. -->

## How I checked it

<!-- Commands you ran, tests, screenshots, measurements. -->

- [ ] Ran it / tested it locally
- [ ] Needs hardware to verify (camera, Muse, wristband, RC car): <!-- say which, and whether you tried it -->

## Breaking change?

- [ ] No
- [ ] Yes: <!-- what breaks (message fields, topic names, file formats…) and who needs to update. Title must have "!" -->

## Checklist

- [ ] Branch is named `owner/what-it-implements` (e.g. `zoe/bytetrack-tracking`)
- [ ] Every commit follows `type(scope): description`
- [ ] Follows the shared conventions in `CLAUDE.md` (metres, degrees, `pylsl.local_clock()`, `"v": 1` in messages)
- [ ] Updated README / `CLAUDE.md` if this changes a rule or a convention
- [ ] No secrets, tokens, recordings or datasets committed
