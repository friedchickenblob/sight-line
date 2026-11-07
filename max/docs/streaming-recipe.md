# Streaming recipe: Muse S → Python (guide p.3)

> ⏳ **UNVERIFIED: waiting for hardware.** Written from BrainFlow's Muse driver source
> and tested end to end on BrainFlow's synthetic board, not on our headband yet. The goal
> (guide p.3 DONE WHEN) is a cold start in under 2 minutes, every time. Time yourself and
> fix this page until it's true.

## 0 · Which headband is it?

**Muse S (Gen 2).** That decides the tool: BrainFlow's `MUSE_S_BOARD` talks to it
directly over Bluetooth, so there is no muselsl, BlueMuse or LSL stream to start first.
(The newer Muse S Athena uses a different protocol and a different BrainFlow board;
the code here does not support it.)

Firmware (Muse app → settings): `__________`

## 1 · Install (once, on Windows: WSL can't see Bluetooth)

```bash
cd max
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

The Muse must **not** be connected to the Muse phone app or any other program, or
BrainFlow won't find it. Turn the phone's Bluetooth off if in doubt.

## 2 · Stream and check it

One terminal, one command: the script opens the headband itself.

```bash
python scripts/record.py --out data/raw/first_30s.csv --seconds 30
python scripts/check_stream.py data/raw/first_30s.csv --plot out/first_30s.png
```

If several Muses are around, pick ours by its Bluetooth name or address:

```bash
python scripts/record.py --name MuseS-XXXX --out ...      # name as shown in Windows Bluetooth settings
python scripts/record.py --mac 00:55:DA:XX:XX:XX --out ...
```

`check_stream` prints the p.3 checklist: rate ≈ 52 Hz, dropped packets per minute,
~0 °/s when still (plus the still noise), tens to hundreds of °/s when turning, accel ≈ 1 g.

No headband to hand? `--board synthetic` runs the same BrainFlow code path against
BrainFlow's fake board (250 Hz random data, so the checklist fails, but everything runs).

## What BrainFlow gives us, and what our code does with it

| | BrainFlow (`MUSE_S_BOARD`, auxiliary preset) | Our code |
|---|---|---|
| Gyro | 3 axes, 52 Hz, °/s (scale 0.0074768 per count, clips at ~245 °/s) | as is |
| Accel | 3 axes, 52 Hz, g | as is |
| Timestamps | wall-clock time when each 3-sample packet **arrived** | moved onto `pylsl.local_clock()` (Hanh's camera uses it), then de-jittered from the packet counter |
| Packet counter | the Muse's 16-bit packet number on every row | a skip = a lost packet → `DROP` sample. Exact, even for one lost packet (58 ms), which the guide's 100 ms rule misses |
| Battery | not available for the Muse S | `muse_health.battery_pct` stays `null` |
| Disconnect | data just stops | after 3 s with no data the session is reopened automatically (B3) |

The default Muse preset is `p21` (EEG + gyro + accel). `--muse-preset` passes another
(e.g. `p50` adds the 5th EEG channel, `p61` PPG) if Jordan needs it later.

## B5 · USB Bluetooth dongle

BrainFlow also supports the Muse through a **BLED112** USB dongle (not any dongle: this
one has its own Bluetooth stack):

```bash
python scripts/record.py --board muse_s_bled --serial-port COM4 --out data/raw/b5_3m.csv
```

A generic USB Bluetooth adapter instead just replaces the laptop's own radio: use the
normal `--board muse_s` with the built-in Bluetooth disabled in Device Manager.

| Setup | Cold start time | Drops/min (2 min, 1 m away) | Notes |
|---|---|---|---|
| built-in Bluetooth | | | |
| generic USB adapter | | | |
| BLED112 | | | |

## Troubleshooting (fill in as we hit things)

- `could not start the Muse over BrainFlow (... UNABLE_TO_OPEN_PORT_ERROR / BOARD_NOT_READY ...)`:
  the band is off, already connected elsewhere (Muse app), or out of range. Switch it
  on, close the Muse app, then retry. `--timeout 30` gives the search longer.
- Rate well below 52 Hz or lots of drops: move the laptop closer, face the band, try a
  dongle (B5 above and in [experiments.md](experiments.md)).
