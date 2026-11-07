# Windows setup: from a fresh laptop to live head yaw

Step by step, for Windows 10 or 11 with the Muse S (Gen 2). Everything runs in
**Windows itself**, not WSL: WSL can't see Bluetooth, so it can't reach the Muse.
WSL is fine for editing and git only.

Commands below work in **PowerShell** unless a step says otherwise.

---

## 1 · Install the tools (once)

1. **Python 3.11 or 3.12, 64-bit** from [python.org/downloads](https://www.python.org/downloads/windows/).
   On the first installer screen tick **"Add python.exe to PATH"**.
   Check it in a *new* PowerShell window:
   ```powershell
   python --version        # Python 3.12.x
   ```
2. **Git for Windows** from [git-scm.com](https://git-scm.com/download/win) (defaults are fine).
3. **Bluetooth:** Settings → Bluetooth & devices → Bluetooth **On**. The laptop needs
   Bluetooth Low Energy (any laptop from the last ~8 years has it).
4. *(Optional, only for MQTT)* **Mosquitto** broker from
   [mosquitto.org/download](https://mosquitto.org/download/). See step 8.

## 2 · Get the code

```powershell
cd $HOME\Documents
git clone git@github.com:friedchickenblob/sight-line.git
cd sight-line
git switch sean/live-dashboard        # until it's merged into main
git config core.hooksPath .githooks   # same as `make hook`; Windows usually has no make
```

No SSH key on GitHub? Use `git clone https://github.com/friedchickenblob/sight-line.git` instead.

## 3 · Create the Python environment (once)

```powershell
cd max
python -m venv .venv
.venv\Scripts\Activate.ps1           # cmd: .venv\Scripts\activate.bat   Git Bash: source .venv/Scripts/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Your prompt now starts with `(.venv)`. **Every new terminal needs the activate line
again** (from the `max` folder) before running anything below.

## 4 · Check the install (no headband needed)

```powershell
python -m pytest                                      # all tests should pass (~1-2 min)
python dashboard\app.py --source synthetic:joint      # fake session, played in real time
```

Open <http://127.0.0.1:5000>. You should see live charts and yaw moving.
Stop the server with **Ctrl+C** in the terminal.

`--board synthetic` tests the real BrainFlow code path with BrainFlow's fake board:

```powershell
python dashboard\app.py --board synthetic
```

## 5 · Connect the Muse S

1. Charge it, then switch it on.
2. **Close the Muse app on your phone** and, to be safe, turn the phone's Bluetooth off.
   The Muse talks to only one device at a time.
3. **Don't pair it in Windows Bluetooth settings.** BrainFlow finds it on its own.
4. Record 30 s to check the stream:
   ```powershell
   python scripts\record.py --out data\raw\first_30s.csv --seconds 30
   python scripts\check_stream.py data\raw\first_30s.csv --plot out\first_30s.png
   ```
   Stand still for the first 5 s, then turn your head a few times. `check_stream` should
   tick: about 52 Hz, about 0 drops/min, near 0 °/s when still, tens to hundreds of °/s
   when turning, and accel about 1 g.

More detail: [streaming-recipe.md](streaming-recipe.md).

## 6 · Run the live dashboard

```powershell
python dashboard\app.py
```

Open <http://127.0.0.1:5000>, then:

1. Wear the band, face the start line (+x) and **stand still for 5 s**. The event log
   says `bias [...] deg/s` when it's ready, and yaw starts at 0.
2. **Start recording** (optionally type a name first, e.g. `d2_worn`). Each recording
   should also begin with 5 s standing still: the analysis scripts measure bias from it.
3. **Mark** whenever the experiment says "MARK" (e.g. arriving at a tape line).
4. **Re-zero** sets yaw back to 0: face the start line first.
5. **Stop recording.** The file is in `max\data\raw\<name>.csv`.

Recordings are **not** committed to git (CLAUDE.md). Copy them to the team Drive.

To open the dashboard from a phone or another laptop on the same Wi-Fi:

```powershell
python dashboard\app.py --host 0.0.0.0
```

Then browse to `http://<this-laptop's-IP>:5000` (find the IP with `ipconfig`, under
"IPv4 Address"). Allow access when the Windows Firewall asks.

## 7 · Other scripts

| What | Command |
|---|---|
| Terminal-only live yaw | `python scripts\live_yaw.py` |
| Record from the terminal (Enter = MARK) | `python scripts\record.py --out data\raw\<name>.csv` |
| Analyse an experiment | `python scripts\analyze.py <axis\|drift\|scale\|...> data\raw\<name>.csv` (see [experiments.md](experiments.md)) |
| Replay a recording through the dashboard | `python dashboard\app.py --source replay:data\raw\<name>.csv` |

## 8 · MQTT (optional: sending yaw to Jordan's alert rule)

1. Install Mosquitto (step 1). It runs as a Windows service called "Mosquitto Broker".
   If it isn't running: Services app → Mosquitto Broker → Start.
2. Publish while the dashboard runs:
   ```powershell
   python dashboard\app.py --mqtt localhost
   ```
3. Watch the messages in a second terminal:
   ```powershell
   & "C:\Program Files\mosquitto\mosquitto_sub.exe" -t "sightline/#" -v
   ```

---

## Common errors and fixes

### Setup

**`python` is not recognized, or typing `python` opens the Microsoft Store**
Python isn't on PATH, or Windows' Store alias is in the way. Re-run the python.org
installer → *Modify* → tick "Add Python to environment variables". Or turn off
Settings → Apps → Advanced app settings → **App execution aliases** → `python.exe` and
`python3.exe`. Or use the launcher instead: `py -3.12 -m venv .venv`.

**`Activate.ps1 cannot be loaded because running scripts is disabled on this system`**
That's PowerShell's execution policy. Run this once:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```
Or use cmd and `.venv\Scripts\activate.bat`.

**`ModuleNotFoundError: No module named 'flask'` (or `brainflow`, `numpy`, ...)**
The virtual environment isn't active in this terminal. From the `max` folder run
`.venv\Scripts\Activate.ps1`, then retry. If it still fails: `pip install -r requirements.txt`.

**BrainFlow can't load its library (`OSError: ... BoardController.dll` / "the specified module could not be found")**
BrainFlow ships its own DLLs, which need the Microsoft Visual C++ runtime. Install the
**Visual C++ Redistributable (x64)** from Microsoft
([aka.ms/vs/17/release/vc_redist.x64.exe](https://aka.ms/vs/17/release/vc_redist.x64.exe)),
then open a new terminal. Also check that Python is 64-bit:
`python -c "import struct; print(struct.calcsize('P') * 8)"` must print `64`. If not,
install 64-bit Python, delete the `.venv` folder, and redo step 3.

**`RuntimeError: LSL binary library file was not found` (from `pylsl`)**
We use pylsl only for its clock, but it still needs its DLL.
`pip install --upgrade pylsl` usually fixes it. If not, download `liblsl` for Windows
from [github.com/sccn/liblsl/releases](https://github.com/sccn/liblsl/releases), then
point to the DLL:
`setx PYLSL_LIB "C:\path\to\lsl.dll"` (then open a new terminal).

**`make: The term 'make' is not recognized`**
Windows has no `make`. Use `git config core.hooksPath .githooks` (step 2) instead of `make hook`.

**`python -m pytest` says `no tests ran` or can't import `headmotion`**
Run it from the `max` folder (where `pytest.ini` is), not the repo root.

### Connecting the Muse

**`could not start the Muse over BrainFlow (... BOARD_NOT_READY_ERROR ...)` or `UNABLE_TO_OPEN_PORT_ERROR`**
BrainFlow didn't find the band. Check, in order:
1. The Muse is on and charged.
2. The Muse app (phone or tablet) is closed and the phone's Bluetooth is off.
3. Bluetooth is on in Windows.
4. No other script or dashboard is already connected to it: only one program at a time.
5. If you paired it in Windows Settings earlier, remove it there (Bluetooth & devices →
   the Muse → Remove device), then switch the Muse off and on.
6. Give the search longer: `--timeout 30`.

**It connects to the wrong headband (several Muses in the room)**
Pick ours by name or address: `--name MuseS-XXXX` (the name shown when Windows scans
for Bluetooth devices) or `--mac 00:55:DA:XX:XX:XX`.

**Running inside WSL: it never finds the Muse**
WSL has no Bluetooth. Run it from PowerShell or cmd in Windows (the repo is reachable
from both).

**The dashboard says `no gyro data`, and the log shows `reconnecting`**
The Bluetooth link dropped. The code retries every few seconds on its own; it says
`reconnected` when data flows again. If it keeps failing: bring the laptop closer,
make sure nothing blocks the line of sight (your own body counts), and check the
battery. Test B3 in [experiments.md](experiments.md) measures how long this takes.

**Rate well under 52 Hz, or `drops/min` keeps climbing**
Weak Bluetooth. Move the laptop within ~3 m, face the band, and keep away from crowds of
phones and laptops. A USB Bluetooth adapter can help (test B5). The BLED112 dongle is
`--board muse_s_bled --serial-port COM4`; find the port in Device Manager → Ports.

**`check_stream` fails "rate close to 52 Hz" with about 250 Hz**
You ran with `--board synthetic`: that's BrainFlow's fake board, not the Muse. Drop the
option.

### Using it

**Log says `keep still during bias measurement; measuring bias again` over and over**
It needs 5 s of no head movement right after start. Stand or sit still, facing the start
line, until `bias [...]` appears.

**Re-zero does nothing (log: `re-zero ignored: still measuring bias`)**
Same cause: wait for the bias first.

**`note: calibration still has placeholders for: ...`**
That's not an error. The constants in `calibration.json` are guesses until each
experiment has been run on our band. Run the axis experiment first
(`analyze.py axis ... --save`): it sets the signs and the nose axis that yaw depends on.
See [experiments.md](experiments.md).

**Yaw turns the wrong way (left turn makes it go negative)**
That's the `sign` calibration. Record and save the axis experiment:
`python scripts\analyze.py axis data\raw\axis.csv --save`.

**`Start recording` shows `... already exists: pick another name`**
A recording with that name is already in `data\raw`. Type a new name (it never overwrites).

**`OSError: [WinError 10048] Only one usage of each socket address ...`**
Port 5000 is in use, often by a dashboard still running in another terminal. Close
that one, or use another port: `python dashboard\app.py --port 5001`.

**The page loads but stays at "connecting…" / "dashboard disconnected"**
The server stopped (check its terminal for an error) or was restarted: refresh the page.
Some antivirus web filters buffer streaming pages; if it only happens in one browser,
try another, or add `127.0.0.1` to the filter's exceptions.

**Phone can't open `http://<laptop-IP>:5000`**
Start with `--host 0.0.0.0`, allow Python through Windows Firewall (Private networks),
and make sure both devices are on the same Wi-Fi. Guest or eduroam-style networks often
block device-to-device traffic: use a phone hotspot instead.

**`--mqtt localhost` runs, but `mosquitto_sub` shows nothing**
The publisher doesn't fail when no broker is running; messages are simply dropped.
Start the "Mosquitto Broker" service (Services app). Check with
`netstat -an | findstr 1883`: it should show `LISTENING`.

**`My recording isn't in git status`**
That's intended: `data/raw/` is git-ignored, because recordings are never committed.
Copy them to the team Drive.
