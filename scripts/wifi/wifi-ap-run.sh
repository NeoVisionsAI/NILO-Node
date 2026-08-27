#!/usr/bin/env bash
# NILO-Node WiFi AP on the HOST (NiloCardmed-style: uap0 + STA on wlp3s0).
#
# ⚠️  SOLO en el mini PC destino. NO ejecutar en el PC de desarrollo.
#     Requiere: NILO_WIFI_ALLOW_HOST_SCRIPTS=1
#
# Usage:
#   sudo NILO_WIFI_ALLOW_HOST_SCRIPTS=1 ./scripts/wifi/wifi-ap-run.sh start|stop|restart|status
#
# Config via env (set in /opt/nilo-node/.env or export):
#   NILO_INSTALL_DIR, WIFI_STA_INTERFACE, WIFI_AP_INTERFACE, WIFI_AP_IP,
#   WIFI_COUNTRY_CODE, NILO_WIFI_PASSWORD, WIFI_SSID (optional override)

set -euo pipefail

ACTION="${1:-status}"
if [[ "${ACTION}" != "status" && "${NILO_WIFI_ALLOW_HOST_SCRIPTS:-}" != "1" ]]; then
  echo "ERROR: Refusing to modify WiFi. Set NILO_WIFI_ALLOW_HOST_SCRIPTS=1 on the target mini PC only." >&2
  exit 1
fi
INSTALL_DIR="${NILO_INSTALL_DIR:-/opt/nilo-node}"
RUNTIME_DIR="${INSTALL_DIR}/wifi-runtime"
ENV_FILE="${INSTALL_DIR}/.env"
PID_DIR="/run/nilo-node-wifi"

WIFI_STA_INTERFACE="${WIFI_STA_INTERFACE:-}"
WIFI_AP_INTERFACE="${WIFI_AP_INTERFACE:-auto}"
WIFI_AP_IP="${WIFI_AP_IP:-192.168.50.1}"
WIFI_COUNTRY_CODE="${WIFI_COUNTRY_CODE:-ES}"
DHCP_START="${WIFI_DHCP_START:-192.168.50.10}"
DHCP_END="${WIFI_DHCP_END:-192.168.50.100}"
WIFI_CHANNEL="${WIFI_CHANNEL:-6}"

log() { printf '[nilo-wifi-ap] %s\n' "$*"; }
warn() { printf '[nilo-wifi-ap] WARN: %s\n' "$*" >&2; }

[[ "${EUID}" -eq 0 ]] || { echo "Run as root" >&2; exit 1; }

load_env() {
  if [[ -f "${ENV_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
  fi
}

detect_sta_iface() {
  if [[ -n "${WIFI_STA_INTERFACE}" ]]; then
    echo "${WIFI_STA_INTERFACE}"
    return
  fi
  iw dev 2>/dev/null | awk '
    $1=="Interface" {
      n=$2
      if (n ~ /^uap/ || n ~ /-ap$/ || n == "niloap0" || n == "ap0") next
      print n; exit
    }'
}

detect_sta_channel() {
  local sta="$1"
  local ch freq
  ch="$(iw dev "${sta}" link 2>/dev/null | awk '/^channel/ {print $2; exit}')"
  if [[ -n "${ch}" ]]; then
    echo "${ch}"
    return
  fi
  freq="$(iw dev "${sta}" link 2>/dev/null | awk '/freq:/ {print $2; exit}')"
  if [[ -n "${freq}" && "${freq}" -ge 5170 ]]; then
    echo $(( (freq - 5000) / 5 ))
    return
  fi
  echo "${WIFI_CHANNEL}"
}

resolve_ap_name() {
  local sta="$1"
  local cfg="${2:-auto}"
  if [[ -z "${cfg}" || "${cfg}" == "auto" ]]; then
    echo "${sta}-ap"
  else
    echo "${cfg}"
  fi
}

cleanup_phy_ap_ifaces() {
  local sta="$1"
  local name typ
  while read -r name typ; do
    [[ -z "${name}" || "${name}" == "${sta}" ]] && continue
    if [[ "${typ}" == "AP" || "${typ}" == "__ap" ]]; then
      log "Removing stale AP ${name}"
      iw dev "${name}" del 2>/dev/null || true
    fi
  done < <(iw dev 2>/dev/null | awk '
    $1=="Interface"{n=$2; t=""}
    $1=="type"{t=$2; if (n!="") print n, t}')
  for ghost in "$(resolve_ap_name "${sta}" "${WIFI_AP_INTERFACE}")" "${sta}-ap" niloap0 uap0; do
    ip link del "${ghost}" 2>/dev/null || iw dev "${ghost}" del 2>/dev/null || true
  done
  sleep 0.5
}

get_node_id() {
  local vol_path id
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx nilo-node; then
    id="$(docker exec nilo-node cat /data/node_id 2>/dev/null || true)"
    [[ -n "${id}" ]] && { echo "${id}"; return; }
  fi
  vol_path="$(docker volume inspect nilo-node_nilo-data -f '{{.Mountpoint}}' 2>/dev/null || true)"
  if [[ -n "${vol_path}" && -f "${vol_path}/node_id" ]]; then
    cat "${vol_path}/node_id"
    return
  fi
  echo ""
}

build_ssid() {
  if [[ -n "${WIFI_SSID:-}" ]]; then
    echo "${WIFI_SSID}"
    return
  fi
  local node_id short_id
  node_id="$(get_node_id)"
  short_id="${node_id//-/}"
  short_id="${short_id:0:8}"
  echo "nilo-node-${short_id:-local}"
}

ensure_ap_iface() {
  local sta="$1" ap_cfg="$2"
  local ap try_names created=""
  rfkill unblock wifi 2>/dev/null || true
  iw reg set "${WIFI_COUNTRY_CODE}" 2>/dev/null || true
  cleanup_phy_ap_ifaces "${sta}"

  try_names="$(resolve_ap_name "${sta}" "${ap_cfg}") ${sta}-ap niloap0 uap0"
  for ap in ${try_names}; do
    [[ "${ap}" == "${sta}" ]] && continue
    iw dev "${ap}" del 2>/dev/null || ip link del "${ap}" 2>/dev/null || true
    sleep 0.2
    if iw dev "${sta}" interface add "${ap}" type __ap 2>/dev/null; then
      log "Created virtual AP ${ap} on ${sta}"
      created="${ap}"
      break
    fi
  done

  if [[ -n "${created}" ]]; then
    AP_MODE="concurrent"
    WIFI_AP_INTERFACE="${created}"
    ap="${created}"
  else
    warn "Virtual AP not supported — dedicated mode on ${sta}"
    AP_MODE="dedicated"
    if command -v nmcli >/dev/null 2>&1; then
      nmcli device disconnect "${sta}" 2>/dev/null || true
      nmcli device set "${sta}" managed no 2>/dev/null || true
    fi
    WIFI_AP_INTERFACE="${sta}"
    ap="${sta}"
  fi

  if [[ "${AP_MODE}" == "concurrent" ]]; then
    if command -v nmcli >/dev/null 2>&1; then
      nmcli device set "${ap}" managed no 2>/dev/null || true
    fi
    ip link set "${ap}" up 2>/dev/null || true
  fi
}

write_configs() {
  local ap="$1" ssid="$2" pass="$3"
  mkdir -p "${RUNTIME_DIR}" "${PID_DIR}"
  cat > "${RUNTIME_DIR}/hostapd.conf" <<EOF
interface=${ap}
driver=nl80211
ssid=${ssid}
channel=${WIFI_CHANNEL}
country_code=${WIFI_COUNTRY_CODE}
ieee80211d=1
hw_mode=g
ieee80211n=1
wmm_enabled=1
auth_algs=1
ignore_broadcast_ssid=0
EOF
  if [[ "${WIFI_CHANNEL}" -gt 14 ]]; then
    sed -i 's/hw_mode=g/hw_mode=a/' "${RUNTIME_DIR}/hostapd.conf"
    echo "ieee80211ac=1" >> "${RUNTIME_DIR}/hostapd.conf"
  fi
  if [[ -n "${pass}" ]]; then
    cat >> "${RUNTIME_DIR}/hostapd.conf" <<EOF
wpa=2
wpa_passphrase=${pass}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF
  fi

  cat > "${RUNTIME_DIR}/dnsmasq.conf" <<EOF
interface=${ap}
bind-interfaces
except-interface=lo
port=0
no-resolv
no-hosts
dhcp-authoritative
dhcp-range=${DHCP_START},${DHCP_END},12h
dhcp-option=option:router,${WIFI_AP_IP}
dhcp-option=option:dns-server,${WIFI_AP_IP}
EOF
}

start_ap() {
  load_env
  local sta ssid pass ap
  sta="$(detect_sta_iface)"
  [[ -n "${sta}" ]] || { warn "No WiFi STA interface"; return 1; }
  ap="${WIFI_AP_INTERFACE}"
  ssid="$(build_ssid)"
  pass="${NILO_WIFI_PASSWORD:-}"
  WIFI_CHANNEL="$(detect_sta_channel "${sta}")"

  ensure_ap_iface "${sta}" "${ap}"
  ap="${WIFI_AP_INTERFACE}"
  write_configs "${ap}" "${ssid}" "${pass}"

  ip link set "${ap}" up
  ip addr flush dev "${ap}" 2>/dev/null || true
  ip addr replace "${WIFI_AP_IP}/24" dev "${ap}" 2>/dev/null || true

  pkill -f "${RUNTIME_DIR}/hostapd.conf" 2>/dev/null || true
  pkill -f "${RUNTIME_DIR}/dnsmasq.conf" 2>/dev/null || true
  sleep 0.3

  hostapd -B -P "${PID_DIR}/hostapd.pid" "${RUNTIME_DIR}/hostapd.conf"
  dnsmasq --conf-file="${RUNTIME_DIR}/dnsmasq.conf" --pid-file="${PID_DIR}/dnsmasq.pid" -k &
  sleep 0.5

  if iw dev "${ap}" info 2>/dev/null | grep -q 'type AP'; then
    log "AP active: ssid=${ssid} mode=${AP_MODE} sta=${sta} ap=${ap} ip=${WIFI_AP_IP}"
    return 0
  fi
  warn "hostapd running but ${ap} is not type AP"
  return 1
}

stop_ap() {
  pkill -f "${RUNTIME_DIR}/hostapd.conf" 2>/dev/null || true
  pkill -f "${RUNTIME_DIR}/dnsmasq.conf" 2>/dev/null || true
  rm -f "${PID_DIR}/hostapd.pid" "${PID_DIR}/dnsmasq.pid"
  log "AP stopped"
}

status_ap() {
  local sta ap
  sta="$(detect_sta_iface)"
  ap="${WIFI_AP_INTERFACE}"
  echo "STA interface: ${sta:-?}"
  echo "AP interface:  ${ap}"
  if [[ -n "${ap}" ]] && iw dev "${ap}" info 2>/dev/null; then
    echo ""
  fi
  pgrep -af 'hostapd|dnsmasq' 2>/dev/null | grep -E 'nilo|wifi-runtime' || echo "(no nilo hostapd/dnsmasq)"
  curl -sf "http://${WIFI_AP_IP}:8080/api/v1/health" >/dev/null && echo "HTTP OK on ${WIFI_AP_IP}:8080" || echo "HTTP not reachable on ${WIFI_AP_IP}:8080"
}

load_env
case "${ACTION}" in
  start) start_ap ;;
  stop) stop_ap ;;
  restart) stop_ap; start_ap ;;
  status) status_ap ;;
  *) echo "Usage: $0 start|stop|restart|status" >&2; exit 1 ;;
esac
