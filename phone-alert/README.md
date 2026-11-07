# Phone alert: laptop to Android over Wi-Fi or a hotspot

A small web server on the Windows laptop and an Android app. Send a notification from the
laptop (a browser form, `curl`, or MQTT) and the phone buzzes and shows it within a fraction
of a second. No internet, no Google services and no router needed: the phone only has to
reach the laptop over a local network, so a hotspot is enough.

```
 browser / curl / MQTT ──► server.py (laptop, port 8765) ══ one open HTTP connection ══► Android app ──► notification + buzz
```

The app keeps one connection open to the server (Server-Sent Events) from a foreground
service, and reconnects by itself when the hotspot drops.

## 1. Run the server (laptop)

From the repo's `max/` virtual environment (it already has Flask) or any Python 3.10+:

```bash
cd phone-alert/server
pip install -r requirements.txt
python server.py
```

It prints the addresses the phone can use, for example `172.28.6.89:8765`.
Open <http://localhost:8765/> for a page with a send form and a list of connected phones.

**Allow it through Windows Firewall (once).** In an Administrator PowerShell:

```powershell
cd phone-alert\server
Set-ExecutionPolicy -Scope Process Bypass
.\allow-firewall.ps1
```

Without this the phone often cannot connect, because Windows treats a hotspot network as
"Public" and blocks inbound connections on it. The rule only admits the local subnet.

Optional: `python server.py --token mysecret` makes the phone and every sender prove they know it
(`X-Token` header). Do this if other people share the hotspot.

## 2. Install the app (phone)

The APK is built already at `phone-alert/android/build/SightLineAlert.apk` (rebuild with
`android\build.ps1`, see below). Either:

- **Over USB:** turn on Developer options and USB debugging, then
  `adb install -r phone-alert\android\build\SightLineAlert.apk`
- **By file:** copy the APK to the phone (USB, Drive, Bluetooth), open it, and allow "Install
  unknown apps" for the app you opened it from. Android may show a Play Protect warning for
  apps that are not from the store; this one is signed with your own debug key.

Open **SightLine Alert**, allow notifications, and press **Allow running in background** → choose
**Unrestricted** (otherwise Android may stop the connection when the screen is off).

## 3. Connect the two (pick one hotspot setup)

| Setup | How | Laptop address |
|---|---|---|
| **A. Phone hotspot** (most reliable) | Turn on the phone's hotspot, join it from the laptop | Press **Find server** in the app. The laptop's address is whatever the phone gave it |
| **B. Laptop hotspot** | Windows Settings → Network & internet → Mobile hotspot → On, join it from the phone | Usually `192.168.137.1:8765` (the app tries this too) |
| **C. Same Wi-Fi** | Both on one router | Press **Find server**, or type the address the server printed |

Then press **Start**. The status box turns to **Connected**, and the server page lists the phone.
Press **Test notification on this phone** to check sound and vibration without the laptop.

Neither hotspot needs internet. The app deliberately sends its traffic over the Wi-Fi network,
because Android otherwise switches to mobile data when the Wi-Fi has no internet and then cannot
reach the laptop.

## Sending alerts

```bash
curl -X POST http://localhost:8765/notify -H "Content-Type: application/json" \
     -d '{"v":1,"title":"Truck approaching","message":"Look left","level":"alert"}'
```

| Field | Meaning |
|---|---|
| `v` | schema version, currently 1 (repo convention) |
| `title`, `message` | text shown on the phone |
| `level` | `info` (normal), `warn` (heads-up plus short buzz), `alert` (heads-up plus long buzz). Default `alert` |
| `ttl_s` | optional: if the phone was offline longer than this, it does not get the alert later |

The reply says how many phones received it: `{"ok":true,"id":7,"delivered_to":1}`. A phone that drops and
reconnects within 30 seconds is sent what it missed.

From Python on the same laptop:

```python
import requests
requests.post("http://localhost:8765/notify", json={"v": 1, "title": "Head not on truck", "level": "alert"})
```

**MQTT.** `python server.py --mqtt localhost` also forwards any message on `sightline/alert` (change with
`--mqtt-topic`) using the same JSON. The topic name is a proposal: the team's message contract is not final, so
check it with Jordan before anything publishes to it.

## Rebuild the app

```powershell
cd phone-alert\android
.\build.ps1
```

It uses only the Android SDK and Android Studio's bundled JDK (no Gradle, nothing downloaded) and writes
`build\SightLineAlert.apk`. The package is `com.sightline.alert`; the display name is in
`app/src/main/res/values/strings.xml`.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| "No server found" / "Can't reach ..." | Run `allow-firewall.ps1`. Check both devices are on the same hotspot, and that `http://<laptop-ip>:8765/health` opens in the phone's browser |
| Wrong address found | The laptop has several adapters (VMware, Hyper-V). Use the address on the hotspot's subnet |
| "Wrong token" | The server was started with `--token`; type the same one in the app |
| Connected but no buzz | Notification category "Alerts" muted or phone in Do Not Disturb. Long-press a notification to check its settings |
| Stops after a while with screen off | Set battery to Unrestricted for the app. Some makers (Xiaomi, Huawei, Samsung) also have their own "auto-start" or "sleeping apps" lists |
| Phone hotspot blocks laptop | Some phones isolate hotspot clients. Try the laptop hotspot (B) instead |

## Limits

- Tested end to end on the Android 16 emulator and with the server's automated tests (`pytest phone-alert/server`).
  Not yet tried on a physical phone or an actual hotspot, so the first real-phone run is the check.
- Traffic is plain HTTP on the local network, with an optional shared token. Do not expose the port to the internet.
- Alerts go to every connected phone. There is no per-device targeting.
