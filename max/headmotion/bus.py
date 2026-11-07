"""MQTT in and out (the repo's message transport).

    pub = Publisher("localhost")          # broker host; Sean runs it on the demo laptop
    pub.publish(head_yaw_msg)             # -> sightline/head_yaw
    cam = CameraYawSubscriber("sightline/head_yaw_cam", "localhost")
    LiveMuse(extra=[cam])                 # camera frames arrive as CAM samples

WAITING ON HANH: the camera-yaw topic and its field names are not agreed yet.
The subscriber assumes the repo convention: {"v": 1, "t_s": ..., "yaw_deg": ...},
with t_s on the same laptop's pylsl.local_clock(), and an optional "valid": false
for frames where the marker did not read cleanly.
"""
from __future__ import annotations

import json
import threading
from typing import Optional

from .messages import SCHEMA_VERSION
from .sources import CAM, Sample


def _client(client_id: str = ""):
    import paho.mqtt.client as mqtt
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)


def parse_host(spec: str) -> tuple[str, int]:
    """'host' or 'host:port' -> (host, port)."""
    host, _, port = spec.partition(":")
    return host or "localhost", int(port) if port else 1883


class Publisher:
    """Publishes HeadYaw / Contact / MuseHealth to their topics (QoS 0: a late yaw is a useless yaw)."""

    def __init__(self, host: str = "localhost", port: int = 1883, client=None):
        self._c = client or _client()
        if client is None:
            self._c.connect_async(host, port)
            self._c.loop_start()
        self.sent = 0

    def publish(self, msg) -> None:
        self._c.publish(msg.TOPIC, msg.to_json(), qos=0)
        self.sent += 1

    def close(self):
        self._c.loop_stop()
        self._c.disconnect()


class CameraYawSubscriber:
    """Hanh's camera yaw over MQTT -> CAM samples, collected by LiveMuse through drain()."""

    def __init__(self, topic: str, host: str = "localhost", port: int = 1883, client=None,
                 t_field: str = "t_s", yaw_field: str = "yaw_deg"):
        self.topic, self.t_field, self.yaw_field = topic, t_field, yaw_field
        self.rejected = 0
        self._buf: list[Sample] = []
        self._lock = threading.Lock()
        self._c = client or _client()
        self._c.on_connect = self._on_connect
        self._c.on_message = self._on_message
        if client is None:
            self._c.connect_async(host, port)
            self._c.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        client.subscribe(self.topic, qos=0)       # here, so a reconnect subscribes again

    def _on_message(self, client, userdata, msg):
        s = self.parse(msg.payload)
        with self._lock:
            if s is None:
                self.rejected += 1
            else:
                self._buf.append(s)

    def parse(self, payload) -> Optional[Sample]:
        try:
            d = json.loads(payload)
            if d.get("v") != SCHEMA_VERSION or d.get("valid") is False or d.get(self.yaw_field) is None:
                return None
            return Sample(CAM, float(d[self.t_field]), (float(d[self.yaw_field]),))
        except (ValueError, KeyError, TypeError, AttributeError):
            return None

    def drain(self) -> list[Sample]:
        with self._lock:
            out, self._buf = self._buf, []
        return out

    def close(self):
        self._c.loop_stop()
        self._c.disconnect()
