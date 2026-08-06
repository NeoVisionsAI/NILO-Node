#!/usr/bin/env python3
"""Apply production PoE camera settings to config/nilo-node.yaml (idempotent)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

POE_CAMERA = {
    "device_ip": "169.254.1.222",
    "connection_mode": "poe",
    "mock_when_unavailable": False,
    "auto_connect": True,
    "enabled": True,
}


def apply_poe_camera(config_path: Path) -> None:
    if not config_path.is_file():
        raise FileNotFoundError(f"Config not found: {config_path}")

    with config_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    camera = data.setdefault("camera", {})
    changed: list[str] = []

    for key, value in POE_CAMERA.items():
        if camera.get(key) != value:
            camera[key] = value
            changed.append(key)

    if not changed:
        print(f"[patch-config] PoE camera settings already applied: {config_path}")
    else:
        with config_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)

        print(f"[patch-config] Updated {config_path}: camera.{', camera.'.join(changed)}")

    oak_local = config_path.parent / "oak.local.yaml"
    if not oak_local.is_file() or changed:
        oak_payload = {"camera": dict(POE_CAMERA)}
        with oak_local.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(oak_payload, fh, sort_keys=False, allow_unicode=True)
        print(f"[patch-config] Wrote {oak_local}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply PoE camera settings to nilo-node.yaml")
    parser.add_argument(
        "config",
        nargs="?",
        default="/opt/nilo-node/config/nilo-node.yaml",
        help="Path to nilo-node.yaml (default: /opt/nilo-node/config/nilo-node.yaml)",
    )
    args = parser.parse_args()
    try:
        apply_poe_camera(Path(args.config))
    except FileNotFoundError as exc:
        print(f"[patch-config] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
