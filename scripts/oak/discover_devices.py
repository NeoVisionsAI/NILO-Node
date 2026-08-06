#!/usr/bin/env python3
"""List OAK devices reachable via PoE (Ethernet) or USB."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from nilo_node.camera.device_connect import format_devices_report, list_devices
from nilo_node.camera.oak_settings import load_oak_connection_settings, persist_discovered_oak_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover OAK cameras (PoE + USB)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--save",
        action="store_true",
        help="Persist first PoE device (or any device) to config/oak.local.yaml",
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Print loaded OAK settings from config files (no hardware required)",
    )
    args = parser.parse_args()

    if args.show_config:
        settings = load_oak_connection_settings()
        print(
            f"device_ip={settings.device_ip or '-'} "
            f"connection_mode={settings.connection_mode} "
            f"device_id={settings.device_id or '-'}"
        )
        return

    try:
        devices = list_devices()
    except ImportError:
        print("depthai not installed", file=sys.stderr)
        raise SystemExit(1) from None

    saved_path: Path | None = None
    if args.save and devices:
        for device in devices:
            if device.get("connection") == "poe" or device.get("ip"):
                saved_path = persist_discovered_oak_settings(device)
                break
        if saved_path is None:
            saved_path = persist_discovered_oak_settings(devices[0])

    if args.json:
        import json

        print(json.dumps(devices, indent=2))
    else:
        print(format_devices_report(devices))
        if saved_path is not None:
            print(f"\nSaved OAK settings to {saved_path}", file=sys.stderr)
        if not devices:
            print(
                "\nPoE tip: sudo POE_IFACE=<eth> ./scripts/oak/setup-poe-network.sh\n"
                "That writes config/oak.local.yaml — then: ./scripts/oak/run-in-docker.sh tof\n"
                "(Or: discover --save after a successful find)",
                file=sys.stderr,
            )
            raise SystemExit(1)


if __name__ == "__main__":
    main()
