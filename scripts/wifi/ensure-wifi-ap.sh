#!/usr/bin/env bash
# One-time / idempotent host setup for NILO-Node WiFi AP (NiloCardmed-style).
#
# ⚠️  SOLO en el mini PC destino. NO ejecutar en el PC de desarrollo.
#     Requiere: NILO_WIFI_ALLOW_HOST_SCRIPTS=1
#
# Usage: sudo NILO_WIFI_ALLOW_HOST_SCRIPTS=1 NILO_INSTALL_DIR=/opt/nilo-node ./scripts/wifi/ensure-wifi-ap.sh

set -euo pipefail

if [[ "${NILO_WIFI_ALLOW_HOST_SCRIPTS:-}" != "1" ]]; then
  echo "ERROR: Refusing to modify WiFi/NM. Set NILO_WIFI_ALLOW_HOST_SCRIPTS=1 on the target mini PC only." >&2
  exit 1
fi

INSTALL_DIR="${NILO_INSTALL_DIR:-/opt/nilo-node}"
AP_IFACE="${WIFI_AP_INTERFACE:-uap0}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

log() { printf '[ensure-wifi-ap] %s\n' "$*"; }

[[ "${EUID}" -eq 0 ]] || { echo "Run as root" >&2; exit 1; }

log "NetworkManager: mark ${AP_IFACE} unmanaged"
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/nilo-node-uap0.conf <<EOF
[keyfile]
unmanaged-devices=interface-name:${AP_IFACE}
EOF
if command -v nmcli >/dev/null 2>&1; then
  systemctl reload NetworkManager 2>/dev/null || true
fi

for svc in hostapd dnsmasq; do
  if systemctl is-enabled "${svc}" >/dev/null 2>&1; then
    systemctl stop "${svc}" 2>/dev/null || true
    systemctl disable "${svc}" 2>/dev/null || true
    systemctl mask "${svc}" 2>/dev/null || true
    log "Masked system service: ${svc}"
  fi
done

install -d "${INSTALL_DIR}/scripts/wifi"
for f in wifi-ap-run.sh prepare-ap-interface.sh diagnose-ap.sh; do
  if [[ -f "${REPO_ROOT}/scripts/wifi/${f}" ]]; then
    install -m 755 "${REPO_ROOT}/scripts/wifi/${f}" "${INSTALL_DIR}/scripts/wifi/${f}"
  fi
done

UNIT=/etc/systemd/system/nilo-node-wifi-ap.service
cat > "${UNIT}" <<EOF
[Unit]
Description=NILO-Node WiFi AP (hostapd on ${AP_IFACE})
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=NILO_INSTALL_DIR=${INSTALL_DIR}
Environment=WIFI_AP_INTERFACE=${AP_IFACE}
ExecStart=${INSTALL_DIR}/scripts/wifi/wifi-ap-run.sh start
ExecStop=${INSTALL_DIR}/scripts/wifi/wifi-ap-run.sh stop

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable nilo-node-wifi-ap.service 2>/dev/null || true
log "Installed ${UNIT} (enable/start after deploy if using backend=host)"

log "Done. For container-managed AP (default), uap0 NM rule is enough."
log "For host-managed AP: set wifi.backend=host in config and: systemctl start nilo-node-wifi-ap"
