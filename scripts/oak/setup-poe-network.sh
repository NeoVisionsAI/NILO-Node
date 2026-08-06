#!/usr/bin/env bash
# Configure mini PC Ethernet for direct OAK-D-SR-PoE link (no DHCP on that cable).
#
# Topology: OAK → PoE injector → mini PC Ethernet
#
# Usage:
#   sudo ./scripts/oak/setup-poe-network.sh
#   sudo POE_IFACE=enp2s0 POE_HOST_IP=192.168.1.10 ./scripts/oak/setup-poe-network.sh
#
# Ubuntu NetworkManager. The "connection failed" toast is expected before this script.

set -euo pipefail

POE_HOST_IP="${POE_HOST_IP:-192.168.1.10}"
POE_PREFIX="${POE_PREFIX:-24}"
POE_CAMERA_IP="${POE_CAMERA_IP:-192.168.1.15}"
POE_IFACE="${POE_IFACE:-}"

log() { printf '[poe-network] %s\n' "$*"; }
die() { printf '[poe-network] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "Run as root: sudo $0"

if ! command -v nmcli >/dev/null 2>&1; then
  die "NetworkManager (nmcli) required"
fi

if [[ -z "${POE_IFACE}" ]]; then
  log "Detecting Ethernet interface (use POE_IFACE=... to override)..."
  POE_IFACE="$(nmcli -t -f DEVICE,TYPE device status | awk -F: '$2=="ethernet"{print $1; exit}')"
fi

[[ -n "${POE_IFACE}" ]] || die "No ethernet interface found. Set POE_IFACE manually."

CONN_NAME="nilo-oak-poe"

log "Interface: ${POE_IFACE}"
log "Host IP:   ${POE_HOST_IP}/${POE_PREFIX}"
log "Camera IP: ${POE_CAMERA_IP} (Luxonis factory default — verify if needed)"

nmcli connection delete "${CONN_NAME}" 2>/dev/null || true

nmcli connection add \
  type ethernet \
  con-name "${CONN_NAME}" \
  ifname "${POE_IFACE}" \
  ipv4.method manual \
  ipv4.addresses "${POE_HOST_IP}/${POE_PREFIX}" \
  ipv4.never-default yes \
  ipv6.method ignore \
  connection.autoconnect yes

nmcli connection up "${CONN_NAME}"

log "PoE link configured. Test:"
log "  ping -c 2 ${POE_CAMERA_IP}"
log "  ./scripts/oak/run-in-docker.sh discover"
log ""
log "Note: This interface has no internet — use WiFi/other NIC for apt/docker hub."
