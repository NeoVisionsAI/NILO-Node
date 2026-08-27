#!/usr/bin/env python3
"""Enable WiFi AP settings in config/nilo-node.yaml (idempotent)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

WIFI_AP = {
    "enabled": True,
    "interface": "auto",
    "ap_interface": "uap0",
    "concurrent_sta_ap": True,
    "backend": "container",
    "mock_when_unavailable": True,
    "configure_interface_ip": True,
}
# hardware_ap is intentionally omitted — set true on mini PC, false on dev laptops.


def apply_wifi_ap(config_path: Path) -> None:
    if not config_path.is_file():
        raise FileNotFoundError(f"Config not found: {config_path}")

    with config_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    wifi = data.setdefault("wifi", {})
    changed: list[str] = []

    for key, value in WIFI_AP.items():
        if wifi.get(key) != value:
            wifi[key] = value
            changed.append(key)

    # Mini PC default when unset; never override explicit dev safety (hardware_ap: false).
    if wifi.get("hardware_ap") is None:
        wifi["hardware_ap"] = True
        changed.append("hardware_ap")

    if not changed:
        print(f"[patch-config] WiFi AP settings already applied: {config_path}")
        return

    with config_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)

    print(f"[patch-config] Updated {config_path}: wifi.{', wifi.'.join(changed)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Enable WiFi AP in nilo-node.yaml")
    parser.add_argument(
        "config",
        nargs="?",
        default="/opt/nilo-node/config/nilo-node.yaml",
    )
    args = parser.parse_args()
    try:
        apply_wifi_ap(Path(args.config))
    except FileNotFoundError as exc:
        print(f"[patch-config] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
