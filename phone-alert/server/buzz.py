"""Start / stop the phone's continuous buzz by hand (app 1.1+), through the phone-alert server.

    python phone-alert/server/buzz.py on            # buzz until Ctrl+C (then it stops)
    python phone-alert/server/buzz.py on -s 5       # buzz for 5 seconds
    python phone-alert/server/buzz.py off           # stop now (e.g. from another terminal)

The app stops by itself if "on" isn't repeated within hold_s, so "on" keeps refreshing it while it runs.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request

HOLD_S, REFRESH_S = 1.5, 0.5


def send(url: str, body: dict, token: str | None) -> int:
    headers = {"Content-Type": "application/json", **({"X-Token": token} if token else {})}
    req = urllib.request.Request(url.rstrip("/") + "/buzz", data=json.dumps({"v": 1, **body}).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.load(r).get("delivered_to", 0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("state", choices=["on", "off"])
    ap.add_argument("-s", "--seconds", type=float, help="with on: stop after this many seconds (default: until Ctrl+C)")
    ap.add_argument("--title", default="Test buzz")
    ap.add_argument("--message", default="from buzz.py")
    ap.add_argument("--url", default="http://127.0.0.1:8765", help="phone-alert server (use 127.0.0.1, not localhost)")
    ap.add_argument("--token", help="the server's --token, if it has one")
    a = ap.parse_args()

    if a.state == "off":
        print(f"buzz off -> {send(a.url, {'on': False}, a.token)} phone(s)")
        return
    on = {"on": True, "hold_s": HOLD_S, "title": a.title, "message": a.message}
    print(f"buzz on -> {send(a.url, on, a.token)} phone(s)"
          + (f" for {a.seconds:g} s" if a.seconds else "; Ctrl+C to stop"))
    end = time.monotonic() + a.seconds if a.seconds else float("inf")
    try:
        while time.monotonic() < end:
            time.sleep(min(REFRESH_S, max(0.0, end - time.monotonic())))
            if time.monotonic() < end:
                send(a.url, on, a.token)
    except KeyboardInterrupt:
        pass
    finally:
        print(f"buzz off -> {send(a.url, {'on': False}, a.token)} phone(s)")


if __name__ == "__main__":
    main()
