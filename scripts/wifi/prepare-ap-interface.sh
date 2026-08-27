#!/usr/bin/env bash
# Host prep for WiFi AP — does NOT create uap0 (the container creates it on start).
#
# ⚠️  SOLO en el mini PC destino. NO ejecutar en el PC de desarrollo.
#     Requiere: NILO_WIFI_ALLOW_HOST_SCRIPTS=1
#
# Usage:
#   sudo NILO_WIFI_ALLOW_HOST_SCRIPTS=1 ./scripts/wifi/prepare-ap-interface.sh

set -euo pipefail

if [[ "${NILO_WIFI_ALLOW_HOST_SCRIPTS:-}" != "1" ]]; then
  echo "ERROR: Refusing to modify WiFi. Set NILO_WIFI_ALLOW_HOST_SCRIPTS=1 on the target mini PC only." >&2
  exit 1
fi

STA_IFACE="${1:-}"
AP_IFACE="${2:-uap0}"
COUNTRY="${WIFI_COUNTRY_CODE:-ES}"

if [[ -z "${STA_IFACE}" ]]; then
  STA_IFACE="$(iw dev 2>/dev/null | awk '$1=="Interface"{print $2; exit}')"
fi

if [[ -z "${STA_IFACE}" ]]; then
  echo "No WiFi interface found." >&2
  exit 1
fi

echo "[wifi-prepare] STA=${STA_IFACE} (AP ${AP_IFACE} lo crea NILO-Node al arrancar)"

rfkill unblock wifi 2>/dev/null || true
iw reg set "${COUNTRY}" 2>/dev/null || true

if command -v nmcli >/dev/null 2>&1; then
  nmcli radio wifi on 2>/dev/null || true
  if ip link show "${AP_IFACE}" &>/dev/null; then
    nmcli device set "${AP_IFACE}" managed no 2>/dev/null || true
    echo "[wifi-prepare] ${AP_IFACE} unmanaged (ya existía)"
  fi
fi

echo "[wifi-prepare] Done."
