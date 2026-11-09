# Running the full SightLine stack

What to start, in what order, and which links to open, for a live demo: phone as the overhead camera, Muse S for head yaw, phone app for the buzz. Written so a human or an AI agent can copy the commands as they are.

Run every command from the repo root (`sight-line/`) using the venv's Python. Here the venv sits one folder up (`../.venv`). If yours is in the repo (`.venv`), change the path. First-time setup is in [SETUP.md](SETUP.md#4-python-environment-once-per-clone).

## The three processes

| # | Process | Port | What it does |
|---|---|---|---|
| 1 | Phone-alert server (`phone-alert/server/server.py`) | 8765 (HTTP) | Control page with the Notifications, Dashboard and Vision tabs, and sends buzzes to the Android app |
| 2 | Head-motion dashboard (`max/dashboard/app.py`) | 5000 (HTTP, laptop only) | Owns the Muse S over Bluetooth and serves head yaw. It comes up with or without the band |
| 3 | Vision server (`vision.run_phone`) | 8443 (HTTPS) | Receives the phone camera over WebRTC, finds the floor, worker and car, and decides when to buzz |

Only one program can hold the Muse, so the dashboard (2) owns it and the vision server reads yaw from the dashboard (`--muse dashboard`).

## Start everything (Git Bash, three terminals or background jobs)

```bash
# 1. Phone-alert server (control page + buzzes)
../.venv/Scripts/python -u phone-alert/server/server.py

# 2. Dashboard + Muse. Comes up straight away; looks for the Muse once in the background.
../.venv/Scripts/python -u max/dashboard/app.py

# 3. Vision server on the phone camera
../.venv/Scripts/python -u -m vision.run_phone --layout floor_layout.json \
  --car-marker 42 --tags-only --muse dashboard --buzz
```

The order doesn't matter. The vision server shows `no dashboard` until the dashboard is up and `hold still` until Muse data arrives, then picks up the yaw by itself. Its buzzes only reach a phone once the phone-alert server is running and the app is connected.

Notes:

- **Marker IDs:** 0–3 are the floor and **42 is the car tag** (`python -m vision.make_markers --ids 42` prints it; keep its white border).
- **Worker:** YOLO's pretrained COCO `person` class. The most confident person becomes the worker and stays the worker while its track ID holds. `--tags-only` makes YOLO look for people only, so the car comes from tag 42 alone. (Optional: `--worker-marker 10` with a tag on the hardhat pins the worker instead.)
- **Small tags missed?** The server looks for markers on a copy shrunk to 960 px wide. Add `--marker-width 0` to search at full resolution (slower).
- **`floor_layout.json`** is git-ignored. Copy `floor_layout.example.json` and fill in the measured marker size and centres ([docs/vision-testing.md](docs/vision-testing.md#1-make-and-lay-out-the-markers)).
- **Muse not found at startup?** Switch it on and press **Search for Muse** on the dashboard (or the Dashboard tab). Each search takes up to ~15 s, and the Events list says whether it connected. Once connected, a dropped band reconnects by itself. From a script: `curl -s -X POST http://127.0.0.1:5000/api/muse/search`.
- **No headband:** in step 2, run `max/dashboard/app.py --source synthetic:joint`.
- Instead of steps 1 and 2, `python phone-alert/server/server.py --dashboard` starts both together.

## Links

`<IP>` is the laptop's address, which servers 1 and 3 print at startup (with a VPN on, take the Wi-Fi IPv4 from `ipconfig` instead).

| Open on | Link | What |
|---|---|---|
| Laptop | <http://localhost:8765/> | **Main control page**: Notifications, Dashboard and Vision tabs |
| Laptop | <http://127.0.0.1:5000/> | Head-motion dashboard on its own, with the **Search for Muse** button |
| Laptop | <https://localhost:8443/overlay> | Vision overlay: floor grid, angle panel, buzz controls |
| Laptop | <https://localhost:8443/view> | Raw phone video with boxes |
| Laptop | <https://localhost:8443/status> · <https://localhost:8443/hazards> | JSON status · live hazard stream (SSE) |
| Laptop | <https://localhost:8443/zero> | Re-zero the heading (worker facing +x) |
| Phone (Chrome) | `https://<IP>:8443` | Camera page: **Advanced → Proceed**, then **Start streaming** |
| Phone (SightLine Alert app) | `<IP>:8765` | Alert server address: **Find server** or type it in, then **Start** |

**Once per browser:** open <https://localhost:8443> on the laptop and accept the certificate warning, or the Vision tab on port 8765 stays empty.

## Connecting things

1. **Network:** phone and laptop on the same Wi-Fi or a hotspot. Campus Wi-Fi often blocks device-to-device traffic, so use the phone's hotspot if it does.
2. **Firewall (once, Administrator PowerShell):** `phone-alert\server\allow-firewall.ps1`. Also allow Python on Private and Public networks when Windows asks (WebRTC uses UDP).
3. **Muse S:** switch it on, close the Muse phone app, and **don't pair it in Windows Bluetooth settings**. If the dashboard didn't find it at startup, press **Search for Muse**. Then the worker stands still facing +x (toward marker 1) for 5 s to calibrate the bias.
4. **Phone camera:** open `https://<IP>:8443` in Chrome, accept the warning and start streaming. Point the camera so all four floor markers are in frame.
5. **Phone buzz:** in the SightLine Alert app (APK at `phone-alert/android/build/SightLineAlert.apk`), connect to `<IP>:8765`. The control page's Notifications tab lists the phone when it's connected.

## Checking it's up

```bash
netstat -ano | grep LISTEN | grep -E ":(5000|8443|8765) "
curl -s http://127.0.0.1:5000/api/state | grep -o '"muse": *{[^}]*}'   # connected / searching
curl -s http://127.0.0.1:8765/health
curl -sk https://127.0.0.1:8443/status
```

Use `127.0.0.1`, not `localhost`, with curl on Windows (`localhost` waits ~2 s on IPv6 first). If the IP changes (new network), restart the vision server with `--new-cert`. More troubleshooting: [phone-alert/README.md](phone-alert/README.md#troubleshooting), [stream_data/README.md](stream_data/README.md#troubleshooting), [docs/vision-testing.md](docs/vision-testing.md#level-3-worker-heading-from-the-muse-s).
