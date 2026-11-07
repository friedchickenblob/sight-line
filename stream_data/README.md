# stream_data: phone camera → computer vision model

A phone opens a link in Chrome and streams its camera over WebRTC to this computer,
where every frame is fed into a computer vision model. Results go back to the phone
and are printed in the terminal.

## Run it (Windows)

1. Put the phone and the computer **on the same Wi-Fi**.
2. Set up once, from this `stream_data` folder in PowerShell:
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1          # cmd: .venv\Scripts\activate.bat
   pip install -r requirements.txt
   ```
3. Start the server:
   ```powershell
   python server.py                    # placeholder model: reports frame size + brightness
   python server.py --model yolo       # object detection (first: pip install ultralytics)
   ```
   It prints the link, e.g. `https://192.168.1.23:8443`.
4. On the phone, open that link in **Chrome**. It warns *"Your connection is not
   private"* (the certificate is self-signed): tap **Advanced → Proceed**.
5. Tap **Start streaming** and allow the camera. You can pick the back or front
   camera, the resolution and the frame rate before starting.
6. The terminal prints once a second, for example
   `phone 1: 30 fps in, 30 fps model (12 ms), 0 dropped, latest {...}`.
   `https://localhost:8443/status` shows the same as JSON. Stop with **Ctrl+C**.

## Watch the live video on the computer

While the phone streams, open **<https://localhost:8443/view>** in a browser on the
computer (accept the same certificate warning). It shows every connected phone's live
video (up to 15 fps; change with `--view-fps 30`) with fps, model time and the latest
result underneath. If the model returns `detections` (as `--model yolo` does), their
boxes and labels are drawn on the video. Other devices on the network can open
`https://<computer's IP>:8443/view` too.

The phone's own preview is on its page, but `/view` shows what the **server** received,
so it's the one to check when debugging the model.

Check it without a phone: `python -m pytest test_server.py` (a Python WebRTC client
plays the phone).

## Plug in your model

Edit [model.py](model.py), or point `--model` at your own code:

```powershell
python server.py --model my_detector:Detector    # my_detector.py in this folder
```

A model is any callable `model(img, t_s) -> dict`:
- `img` is the newest frame as an H × W × 3 `uint8` numpy array in **BGR** (OpenCV order).
- `t_s` is when it arrived, in `pylsl.local_clock()` seconds (the team's shared clock).
- The returned dict (JSON-serialisable) goes back to the phone.

It runs in its own thread on the **newest** frame only. If the model is slower than the
camera, older frames are skipped (counted as "dropped") rather than queued, so results
never fall behind live.

## Troubleshooting

| Problem | Fix |
|---|---|
| Phone can't open the link at all | Same Wi-Fi? Guest, campus and eduroam networks often block device-to-device traffic: use the phone's hotspot and connect the computer to it. Allow Python through **Windows Firewall** (Private networks) when asked. |
| Page says it isn't on HTTPS / Start is greyed out | Use the `https://` link, not `http://`. Chrome only gives the camera to secure pages. |
| "Camera permission denied" | Chrome → ⋮ → Settings → Site settings → Camera: allow this site. Close other apps using the camera. |
| Status goes **connecting → failed** | WebRTC uses UDP on random ports: allow Python in Windows Firewall for **both** Private and Public networks, or try the phone hotspot. Still failing: on the phone open `chrome://flags`, disable *Anonymize local IPs exposed by WebRTC*, and restart Chrome. |
| Wrong IP printed (several network adapters, VPN, WSL) | Find the Wi-Fi adapter's IPv4 with `ipconfig`, use `https://<that IP>:8443`, and run once with `--new-cert` so the certificate includes it. |
| `[WinError 10048]` address already in use | Another server is still running: close it, or use `--port 8444`. |
| Low fps or high "dropped" | The model is the bottleneck: try 640 × 480 on the phone, a smaller model, or a GPU. "dropped" is expected when the model is slower than the camera. |
| Phone screen turns off and the stream stops | The page keeps the screen awake where Chrome allows it. Otherwise raise the phone's screen timeout. |
