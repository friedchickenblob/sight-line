# SightLine

Built for natHACKS 2026 by Jordan, Hanh, Zoe, Sean and Max.

**A proximity alarm that knows whether you've already seen the hazard.** SightLine buzzes a worker only when a machine is heading at them **and** they aren't looking at it. If they've already seen it, it stays quiet.

**Demo video:** [Watch the video](https://drive.google.com/file/d/11vS8G74UPsY7eiGM_7_J2Q-Pj-J6rIXM/view?usp=sharing)

## The problem

On worksites, backup alarms and horns go off every time a machine is nearby, whether or not anyone is paying attention. Workers hear them all shift and learn to tune them out. In the 2019 Fort Hills incident, a worker walking ahead of a reversing truck was killed. The driver honked and checked his mirrors; the warning happened, but nobody was looking. Most systems also alert the driver in the cab, not the person on foot.

## Our solution

SightLine checks for attention instead of assuming it. An overhead camera watches the worker and the machine, and a headband tracks which way the worker's head is pointing. The worker's phone, or later a wristband, buzzes only when both of these are true:

- **the machine is approaching:** it's moving and heading toward the worker;
- **the worker isn't facing it:** their head is turned more than 60° away from the machine.

Fewer pointless alarms means workers keep trusting the ones that matter. Nothing is wired into the machines, and everything runs offline over a phone hotspot.

![image](https://github.com/user-attachments/assets/b4271d73-9d83-4a27-8cfb-b0d18299f14d)


## How it works

1. **Phone camera (overhead)** streams video to a laptop.
2. **Floor map:** four ArUco markers on the floor turn camera pixels into metres.
3. **Find the worker and the machine:** a hardhat marker tags the worker and a marker on top tags the machine, with YOLO + ByteTrack and a dark-blob fallback.
4. **Velocities:** a line fit over the last 0.5 s gives the machine's speed and direction.
5. **Head direction:** the Muse S headband's gyro gives head yaw over Bluetooth.
6. **Alert rule:** approaching (faster than 0.1 m/s, within 60° of the line to the worker) **and** not facing (head more than 60° off).
7. **Buzz:** the laptop calls the alert server, which pushes the alert to the Android app. The buzz stops by itself if the laptop goes silent for 1.5 s.

## Features

- Alerts only when the worker hasn't seen the machine
- Accurate floor tracking: about 3 cm position error on our test floor; hardhat heading within about 3°
- Phone as the camera: no special hardware to stream video
- Works offline on a phone hotspot
- Nothing installed on the machinery; it just needs a marker
- Live overlay and dashboard on the laptop
- JSON hazard messages (optional MQTT on `sightline/vision/hazards`)

## Tech stack

| Part | Hardware | Software |
|---|---|---|
| Camera input | Phone held overhead | WebRTC (`aiortc`, `aiohttp`) |
| Floor mapping | 4 printed ArUco markers | OpenCV, NumPy |
| Detection and tracking | Hardhat and machine markers | Ultralytics YOLO26n + ByteTrack |
| Head direction | Muse S Gen 2 headband | BrainFlow over Bluetooth |
| Alert rule | – | Pure Python geometry |
| Buzz | Android phone (ESP32 wristband planned) | Flask server + Android app over Server-Sent Events |
| Live views | Laptop browser | Overlay page, Flask dashboard, Matplotlib |
| Tests | – | pytest |

Python 3.10+. One `requirements.txt` at the repo root covers every module.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp floor_layout.example.json floor_layout.json   # fill in your measured marker positions

# 1. alert server (then open the SightLine Alert app on the phone)
cd phone-alert/server && python server.py

# 2. vision pipeline
python -m vision.run_phone --layout floor_layout.json --car-marker 42 --worker-marker 10 --tags-only --muse
```

Open the printed `https://<laptop IP>:8443` link on the camera phone and tap **Start streaming**. The worker stands still facing +x for about 5 s to zero the heading. Full setup is in [SETUP.md](SETUP.md).

## Current limits

- Only tracks machines that carry a marker, and one machine at a time
- Markers are small; occlusion and dim light can lose the machine
- When data is missing, it stays silent today. A fail-safe buzz is the next fix
- Doesn't yet weigh distance or time to contact
- Muse heading drifts and needs re-zeroing
- Tested on a 2.25 × 1.5 m floor with one worker

## Future work

- **Before the demo:** a fail-safe buzz when data goes stale; a time-to-contact gate; "looked recently" memory, so a quick glance counts; bigger markers and sharper video
- **Next:** several machines at once; fixed hazard zones (pit edges, swing radius); a Kalman filter to keep tracking through gaps; camera-corrected heading; the ESP32 wristband
- **Later:** detecting machines without markers; scoring hit and false-alarm rates; several workers; a pilot on an Alberta oilsands laydown yard

## Repo layout

| Folder | What it is |
|---|---|
| `vision/` | Overhead camera pipeline: floor map, detection, tracking, alert rule |
| `stream_data/` | Phone camera stream to the laptop over WebRTC |
| `max/` | Muse head motion (yaw, drift, contact maths) and a live dashboard |
| `phone-alert/` | Alert server and Android app |
| `hanh/` | Floor-mapping check, hardhat heading tests and printable markers |


## Team

Jordan · Hanh · Zoe · Sean · Max

**Presentation:** [View the slides](https://docs.google.com/presentation/d/1hu-r9BPmQ32ijDNpcoeCR9FcKZ0D4L4cEIOE1JYtlRU/edit?usp=sharing)
