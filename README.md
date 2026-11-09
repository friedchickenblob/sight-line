# SightLine

Built for natHACKS 2026 by **Team Gyro Benders**: Jordan, Hanh, Zoe, Sean and Max.

**A wearable that warns workers on foot about moving vehicles, but only when a vehicle is coming toward them and they haven't seen it.** Fewer alarms, so the real one gets noticed.

**Demo video:** [Watch the video](https://drive.google.com/file/d/11vS8G74UPsY7eiGM_7_J2Q-Pj-J6rIXM/view?usp=sharing)

## The problem

In June 2025, at a warehouse in BC, a worker was repairing a broken-down forklift. A second forklift backed up and pinned them between the two machines. Investigators found the area congested and poorly lit, and the backup alarms and mirrors were missing or blocked. A worker busy with a task, a forklift backing up, and the usual warnings gone: that's the moment SightLine is built for.

This is far from rare. BC sees about 500 forklift injury claims a year, and in Alberta, 22% of workplace injury claims involve contact with objects or equipment.

Traditional alarms have their own problem: they keep going off even when workers have already seen the moving machine. People get used to the noise and stop paying attention.

## Our solution

The idea is simple: if you've already noticed the hazard, SightLine stays quiet. If you haven't, it gives you a buzz.

To tell whether you've seen it, SightLine looks at your head direction. An overhead camera watches the worker and the vehicle, and a headband tracks which way the worker's head is pointing. If your head pointed at the vehicle (within a 60° field of view) for at least 0.3 s in the last 3 s, that counts as seen. The worker's phone, or later a wristband, buzzes only when both of these are true:

- **the vehicle is approaching:** it's moving and heading toward the worker;
- **the worker hasn't seen it.**

Fewer pointless alarms means workers keep trusting the ones that matter. Nothing is wired into the machines, and everything runs offline over a phone hotspot.

![image](https://github.com/user-attachments/assets/b4271d73-9d83-4a27-8cfb-b0d18299f14d)


## How it works

1. **Phone camera (overhead)** streams video to a laptop.
2. **Floor map:** four ArUco markers on the floor turn camera pixels into metres.
3. **Find the worker and the machine:** tags the worker and a marker on top tags the machine, with YOLO + ByteTrack.
4. **Velocities:** a line fit over the last 0.5 s gives the machine's speed and direction.
5. **Head direction:** the Muse S headband's gyro gives head yaw over Bluetooth.
6. **Alert rule:** approaching (faster than 0.1 m/s, within 60° of the line to the worker) **and** not facing (the vehicle is outside the worker's 60° field of view, 30° either side of their head direction). The current build checks this on every frame; the 0.3 s in 3 s memory is next.
7. **Buzz:** the laptop calls the alert server, which pushes the alert to the Android app. The buzz stops by itself if the laptop goes silent for 1.5 s.

## Features

- Alerts only when the worker hasn't seen the machine
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
- **Next:** several types of machines at once; blink check using eeg; camera-corrected heading; the ESP32 wristband; integrate with existing solutions
- **Later:** detecting machines without markers; scoring hit and false-alarm rates; several workers; a pilot at a warehouse or industrial site

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

**Presentation:** [View the slides](https://docs.google.com/presentation/d/1t13ZtIqsxs16iZcIYVXx7oh5SSoA-M6J/edit?usp=sharing&ouid=101984232930867946887&rtpof=true&sd=true)
