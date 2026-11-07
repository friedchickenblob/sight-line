# SightLine

*Working name. We may rename it DuckDuckTruck.*

A proximity alarm that knows whether you have already seen the hazard. Built for natHACKS 2026 by Jordan, Hanh, Zoe, Sean and Max.

Alarms on worksites go off whenever a machine is nearby, so workers learn to ignore them. SightLine buzzes only when a machine is
about to reach the worker **and** the worker has not looked at it.

## How it works

1. **Overhead camera** tracks the worker and an RC "machine" on the floor (ArUco markers).
2. **Muse S headband** tells us which way the worker's head is pointing.
3. **Decision engine** checks two things: is the machine about to reach the worker, and have they looked at it recently?
4. **Wristband** (an ESP32) buzzes the worker. If the wristband is not ready, the phone app below does the same job.

If the head data goes missing, the system buzzes anyway: when in doubt, warn the worker.

## What's in this repo

| Folder | What it is |
|---|---|
| `max/` | Head motion from the Muse (yaw, drift, contact maths) and a live dashboard |
| `phone-alert/` | Android app and a small server that send alerts from the laptop to a phone, with a control page that also shows the dashboard |
| `muse_collection.py` | Records raw Muse gyro and accelerometer data |
| `.github/` | Issue and PR templates, checks, and the Claude workflows |

## Start here

- **Set up your laptop and the repo:** [SETUP.md](SETUP.md)
- **How we work** (branch names, commit format, pull requests): [CONTRIBUTING.md](CONTRIBUTING.md)
- **Rules for Claude in this repo:** [CLAUDE.md](CLAUDE.md)
- **Phone alerts:** [phone-alert/README.md](phone-alert/README.md)
- **Head motion:** [max/README.md](max/README.md)
