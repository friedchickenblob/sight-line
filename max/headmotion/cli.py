"""Command-line options shared by the scripts that talk to the Muse or MQTT."""
from __future__ import annotations

import argparse

from .sources import BOARDS


def add_source_args(ap: argparse.ArgumentParser):
    ap.add_argument("--source", default="muse", help="muse | replay:<csv> | synthetic:<scene>")
    g = ap.add_argument_group("Muse over BrainFlow (--source muse)")
    g.add_argument("--board", default="muse_s", choices=list(BOARDS),
                   help="muse_s = laptop Bluetooth; muse_s_bled = BLED112 dongle (B5); synthetic = no headband")
    g.add_argument("--name", help="Bluetooth name of the band (e.g. MuseS-1A2B) if several are around")
    g.add_argument("--mac", help="Bluetooth address of the band, instead of --name")
    g.add_argument("--serial-port", help="COM port of the BLED112 dongle (--board muse_s_bled)")
    g.add_argument("--muse-preset", help="Muse preset passed to BrainFlow (default p21: EEG + gyro + accel)")
    g.add_argument("--timeout", type=float, default=15.0, help="seconds to search for the band")
    g = ap.add_argument_group("MQTT")
    g.add_argument("--mqtt", metavar="HOST[:PORT]", help="broker to publish to (and read camera yaw from)")
    g.add_argument("--camera-topic", help="Hanh's camera-yaw topic (WAITING ON HANH to agree the name)")


def muse_kwargs(args, extra=()) -> dict:
    return dict(board=args.board, name=args.name, mac=args.mac, serial_port=args.serial_port,
                muse_preset=args.muse_preset, timeout_s=args.timeout, extra=list(extra))


def camera_subscriber(args):
    """Hanh's camera yaw as an extra sample source, or None."""
    if not args.camera_topic:
        return None
    if not args.mqtt:
        raise SystemExit("--camera-topic needs --mqtt HOST")
    from .bus import CameraYawSubscriber, parse_host
    return CameraYawSubscriber(args.camera_topic, *parse_host(args.mqtt))
