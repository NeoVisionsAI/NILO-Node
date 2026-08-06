#!/usr/bin/env python3
"""List OAK devices reachable via PoE (Ethernet) or USB."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from oak.device_connect import format_devices_report, list_devices  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover OAK cameras (PoE + USB)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        devices = list_devices()
    except ImportError:
        print("depthai not installed", file=sys.stderr)
        raise SystemExit(1) from None

    if args.json:
        import json

        print(json.dumps(devices, indent=2))
    else:
        print(format_devices_report(devices))
        if not devices:
            print(
                "\nPoE tip: sudo POE_IFACE=<eth> ./scripts/oak/setup-poe-network.sh\n"
                "Then: sudo OAK_DEVICE_IP=169.254.1.222 ./scripts/oak/run-in-docker.sh tof\n"
                "(Luxonis camera IP when no DHCP — see docs/POE_SETUP.md)",
                file=sys.stderr,
            )
            raise SystemExit(1)


if __name__ == "__main__":
    main()
