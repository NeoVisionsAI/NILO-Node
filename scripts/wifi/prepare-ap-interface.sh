#!/usr/bin/env bash
# Create uap0 (if supported) and release AP iface from NetworkManager.
# Keeps STA (wlp3s0) connected when concurrent mode works.
#
# ⚠️  SOLO en el mini PC destino. NO ejecutar en el PC de desarrollo.
#     Requiere: NILO_WIFI_ALLOW_HOST_SCRIPTS=1
#
# Usage:
#   sudo NILO_WIFI_ALLOW_HOST_SCRIPTS=1 ./scripts/wifi/prepare-ap-interface.sh
#   sudo NILO_WIFI_ALLOW_HOST_SCRIPTS=1 ./scripts/wifi/prepare-ap-interface.sh wlp3s0 uap0

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

echo "[wifi-prepare] STA=${STA_IFACE} AP=${AP_IFACE}"

rfkill unblock wifi 2>/dev/null || true
iw reg set "${COUNTRY}" 2>/dev/null || true

if ! ip link show "${AP_IFACE}" &>/dev/null; then
  if iw dev "${STA_IFACE}" interface add "${AP_IFACE}" type __ap 2>/dev/null; then
    echo "[wifi-prepare] Created virtual AP ${AP_IFACE}"
  else
    echo "[wifi-prepare] Virtual AP not supported — will use dedicated AP on ${STA_IFACE}"
    AP_IFACE="${STA_IFACE}"
  fi
fi

if command -v nmcli >/dev/null 2>&1; then
  nmcli radio wifi on 2>/dev/null || true
  if [[ "${AP_IFACE}" == "${STA_IFACE}" ]]; then
    nmcli device disconnect "${STA_IFACE}" 2>/dev/null || true
    nmcli device set "${STA_IFACE}" managed no
    echo "[wifi-prepare] Dedicated AP: ${STA_IFACE} unmanaged"
  else
    nmcli device set "${AP_IFACE}" managed no 2>/dev/null || true
    echo "[wifi-prepare] Concurrent: STA=${STA_IFACE} stays managed, AP=${AP_IFACE} unmanaged"
  fi
fi

ip link set "${AP_IFACE}" up 2>/dev/null || true

echo "[wifi-prepare] Done. Restart WiFi AP in NILO-Node."
