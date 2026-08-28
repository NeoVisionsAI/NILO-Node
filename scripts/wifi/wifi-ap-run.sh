#!/usr/bin/env bash
# NILO-Node WiFi AP on the HOST (NiloCardmed-style: uap0 + STA on wlp3s0).
#
# ⚠️  SOLO en el mini PC destino (wifi.hardware_ap=true). NO ejecutar en el portátil dev.
#
# Usage (un solo comando recomendado):
#   sudo ./scripts/wifi/wifi-ap-run.sh up
#
# También: start | stop | restart (=up) | status | check
#
# Config: NILO_INSTALL_DIR, .env (NILO_WIFI_PASSWORD), config/nilo-node.yaml

set -euo pipefail

ACTION="${1:-status}"
_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

sync_paths() {
  INSTALL_DIR="${NILO_INSTALL_DIR:-${_REPO_ROOT}}"
  RUNTIME_DIR="${INSTALL_DIR}/wifi-runtime"
  ENV_FILE="${INSTALL_DIR}/.env"
  PID_DIR="/run/nilo-node-wifi"
  PREFERRED_2G_BSSID_FILE="${RUNTIME_DIR}/preferred-2g-bssid"
}

sync_paths

load_env() {
  if [[ -f "${ENV_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
  fi
  sync_paths
}

load_env

WIFI_STA_INTERFACE="${WIFI_STA_INTERFACE:-}"
WIFI_AP_INTERFACE="${WIFI_AP_INTERFACE:-auto}"
WIFI_AP_IP="${WIFI_AP_IP:-192.168.50.1}"
WIFI_COUNTRY_CODE="${WIFI_COUNTRY_CODE:-ES}"
DHCP_START="${WIFI_DHCP_START:-192.168.50.10}"
DHCP_END="${WIFI_DHCP_END:-192.168.50.100}"
WIFI_CHANNEL="${WIFI_CHANNEL:-6}"
AP_CREATE_PREF=""

log() { printf '[nilo-wifi-ap] %s\n' "$*"; }
warn() { printf '[nilo-wifi-ap] WARN: %s\n' "$*" >&2; }
die() { warn "$*"; exit 1; }

hardware_ap_enabled() {
  local cfg="${INSTALL_DIR}/config/nilo-node.yaml"
  [[ ! -f "${cfg}" ]] && return 0
  python3 - "${cfg}" <<'PY' 2>/dev/null || return 0
import sys, yaml
wifi = (yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}).get("wifi") or {}
sys.exit(1 if wifi.get("hardware_ap") is False else 0)
PY
}

if [[ "${ACTION}" != "status" && "${ACTION}" != "check" ]]; then
  [[ "${EUID}" -eq 0 ]] || die "Ejecuta como root: sudo $0 ${ACTION}"
  hardware_ap_enabled || die "wifi.hardware_ap=false — omitido (seguridad en portátiles dev)"
  export NILO_WIFI_ALLOW_HOST_SCRIPTS=1
fi

sta_link_ssid() {
  local sta="$1"
  iw dev "${sta}" link 2>/dev/null | awk '/SSID:/ { print $2; exit }'
}

sta_is_2ghz() {
  local sta="$1" freq
  freq="$(sta_link_freq_mhz "${sta}")"
  [[ -n "${freq}" && "${freq}" -ge 2412 && "${freq}" -le 2484 ]]
}

report_sta_suitability() {
  local sta="$1" freq ch ssid
  freq="$(sta_link_freq_mhz "${sta}")"
  ch="$(detect_sta_channel "${sta}")"
  ssid="$(sta_link_ssid "${sta}")"
  echo "=== Comprobación red STA (internet del mini PC) ==="
  echo "  Interfaz: ${sta}"
  echo "  SSID:     ${ssid:-?}"
  if [[ -n "${freq}" ]]; then
    echo "  Freq:     ${freq} MHz, canal ${ch}"
  else
    echo "  Freq:     (sin enlace)"
  fi
  if [[ "${WIFI_DEDICATED_AP:-0}" == "1" ]]; then
    echo "  Modo:     AP dedicado (Ethernet) — no requiere STA 2.4 GHz"
    return 0
  fi
  if sta_is_2ghz "${sta}"; then
    echo "  Estado:   ✓ OK — 2.4 GHz, apto para AP+STA concurrente"
    return 0
  fi
  if is_dfs_channel "${ch}" || [[ -n "${freq}" && "${freq}" -ge 5000 ]]; then
    echo "  Estado:   ✗ NO apto — 5 GHz/DFS (AP+STA no funciona en este chipset)"
    echo "  Acción:   reconectar a BSSID 2.4 GHz (automático con: sudo $0 up)"
    return 1
  fi
  echo "  Estado:   ? revisar enlace"
  return 1
}

router_wifi_password() {
  local sta="$1" conn pass
  pass="${WIFI_ROUTER_PASSWORD:-${ROUTER_WIFI_PASSWORD:-}}"
  [[ -n "${pass}" ]] && { printf '%s' "${pass}"; return; }
  command -v nmcli >/dev/null 2>&1 || return 1
  conn="$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null \
    | awk -F: -v d="${sta}" '$2 == d { print $1; exit }')"
  [[ -n "${conn}" ]] || return 1
  nmcli -s -g 802-11-wireless-security.psk connection show "${conn}" 2>/dev/null || true
}

normalize_ssid_base() {
  # MOVISTAR_PLUS_4380 → MOVISTAR_4380 para emparejar 2.4/5G
  local s="$1"
  s="${s/PLUS_/}"
  s="${s/_PLUS/}"
  printf '%s' "${s}"
}

find_2g_bssid() {
  local want_ssid="$1"
  local base preferred line ssid bssid freq
  base="$(normalize_ssid_base "${want_ssid}")"
  if [[ -f "${PREFERRED_2G_BSSID_FILE}" ]]; then
    preferred="$(tr '[:upper:]' '[:lower:]' < "${PREFERRED_2G_BSSID_FILE}" | tr -d '[:space:]')"
    [[ -n "${preferred}" ]] && { echo "${preferred}"; return 0; }
  fi
  command -v nmcli >/dev/null 2>&1 || return 1
  nmcli dev wifi rescan 2>/dev/null || true
  sleep 4
  while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    ssid="${line%%:*}"
    rest="${line#*:}"
    bssid="${rest%%:*}"
    freq="${rest##*:}"
    bssid="${bssid//\\:/:}"
    freq="${freq// MHz/}"
    freq="${freq// /}"
    [[ "${freq}" =~ ^[0-9]+$ ]] || continue
    [[ "${freq}" -lt 2412 || "${freq}" -gt 2484 ]] && continue
    if [[ "${ssid}" == "${want_ssid}" || "${ssid}" == "${base}" ]] \
      || [[ "$(normalize_ssid_base "${ssid}")" == "${base}" ]]; then
      printf '%s' "${bssid}" | tr '[:upper:]' '[:lower:]'
      return 0
    fi
  done < <(nmcli -t -f SSID,BSSID,FREQ dev wifi list 2>/dev/null)
  return 1
}

ensure_sta_2ghz() {
  local sta="$1"
  [[ "${WIFI_DEDICATED_AP:-0}" == "1" ]] && return 0
  [[ "${WIFI_SKIP_STA_FIX:-0}" == "1" ]] && return 0
  if sta_is_2ghz "${sta}"; then
    log "STA ${sta} ya en 2.4 GHz ($(sta_link_freq_mhz "${sta}") MHz)"
    return 0
  fi

  command -v nmcli >/dev/null 2>&1 \
    || die "STA en 5 GHz y nmcli no disponible — conecta manualmente al 2.4 GHz o usa WIFI_DEDICATED_AP=1"

  local ssid pass bssid
  ssid="${WIFI_STA_SSID:-$(sta_link_ssid "${sta}")}"
  [[ -n "${ssid}" ]] || die "No hay SSID activo en ${sta} — conecta el mini PC al WiFi del router primero"

  pass="$(router_wifi_password "${sta}")"
  [[ -n "${pass}" ]] || die "Sin clave del router. Pon WIFI_ROUTER_PASSWORD en ${ENV_FILE} o guarda la red en NetworkManager"

  bssid="$(find_2g_bssid "${ssid}")" || die "No se encontró BSSID 2.4 GHz para SSID ${ssid}. Escanea: nmcli -t -f SSID,BSSID,FREQ dev wifi list"

  log "Reconectando ${sta} a ${ssid} vía 2.4 GHz (BSSID ${bssid})..."
  nmcli dev disconnect "${sta}" 2>/dev/null || true
  sleep 1
  nmcli dev wifi connect "${ssid}" password "${pass}" ifname "${sta}" bssid "${bssid}" \
    || die "nmcli no pudo conectar al BSSID 2.4 GHz"
  sleep 3

  if ! sta_is_2ghz "${sta}"; then
    die "Tras reconectar sigue en $(sta_link_freq_mhz "${sta}") MHz — prueba Ethernet + WIFI_DEDICATED_AP=1"
  fi
  echo "${bssid}" > "${PREFERRED_2G_BSSID_FILE}"
  log "STA en 2.4 GHz ($(sta_link_freq_mhz "${sta}") MHz) — guardado BSSID en ${PREFERRED_2G_BSSID_FILE}"
  return 0
}

full_cleanup() {
  local sta="${1:-}"
  log "Limpieza completa (hostapd, dnsmasq, interfaces AP)..."
  pkill -f "${RUNTIME_DIR}/dnsmasq.conf" 2>/dev/null || true
  rm -f "${PID_DIR}/dnsmasq.pid" 2>/dev/null || true
  kill_nilo_hostapd
  kill_all_hostapd
  rfkill unblock wifi 2>/dev/null || true
  if [[ -n "${sta}" ]]; then
    cleanup_phy_ap_ifaces "${sta}"
  else
    local s
    s="$(detect_sta_iface)"
    [[ -n "${s}" ]] && cleanup_phy_ap_ifaces "${s}"
  fi
  sleep 0.5
  log "Limpieza completada"
}

up_ap() {
  load_env
  local sta
  sta="$(detect_sta_iface)"
  [[ -n "${sta}" ]] || die "No hay interfaz WiFi STA"

  report_sta_suitability "${sta}" || true
  echo ""

  full_cleanup "${sta}"

  if [[ "${WIFI_DEDICATED_AP:-0}" != "1" ]]; then
    ensure_sta_2ghz "${sta}" || return 1
    report_sta_suitability "${sta}" || return 1
    echo ""
  fi

  start_ap_core
  log "✓ Listo — portal http://${WIFI_AP_IP}:8080/setup/  |  comprobar: $0 status"
}

check_ap() {
  load_env
  local sta
  sta="$(detect_sta_iface)"
  [[ -n "${sta}" ]] || { die "No hay interfaz WiFi STA"; }
  if report_sta_suitability "${sta}"; then
    log "Listo para: sudo $0 up"
    exit 0
  fi
  log "Ejecuta: sudo $0 up  (reconectará a 2.4 GHz y arrancará el AP)"
  exit 1
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
  local sta="$1" ch
  ch="$(iw dev "${sta}" link 2>/dev/null | awk '
    /^[[:space:]]*channel/ { print $2; exit }
    /freq:/ {
      f = $2 + 0
      if (f >= 5170) { print int((f - 5000) / 5); exit }
      if (f >= 2412) { print int((f - 2412) / 5) + 1; exit }
    }')"
  if [[ -n "${ch}" ]]; then
    echo "${ch}"
    return
  fi
  echo "${WIFI_CHANNEL}"
}

link_up_benign() {
  local iface="$1"
  ip link set "${iface}" up 2>/dev/null && return 0
  ip -br link show "${iface}" 2>/dev/null | grep -qE '(UP|UNKNOWN)' && return 0
  return 1
}

derive_ap_mac() {
  local sta="$1" mac o1 o2 o3 o4 o5 o6
  mac="$(cat "/sys/class/net/${sta}/address" 2>/dev/null)" || return 1
  IFS=: read -r o1 o2 o3 o4 o5 o6 <<< "${mac}"
  o1=$(printf '%02x' $(( (0x${o1} | 0x02) & 0xfe )))
  o6=$(printf '%02x' $(( (0x${o6} + 1) % 256 )))
  printf '%s:%s:%s:%s:%s:%s\n' "$o1" "$o2" "$o3" "$o4" "$o5" "$o6"
}

phy_for_sta() {
  local sta="$1"
  iw dev "${sta}" info 2>/dev/null | awk '/wiphy/ { print "phy"$2; exit }'
}

detect_ap_iface() {
  local sta="${1:-}"
  iw dev 2>/dev/null | awk -v sta="${sta}" '
    $1=="Interface" { n=$2; t="" }
    $1=="type" {
      t=$2
      if (n == sta || n == "") next
      if (t != "AP" && t != "__ap" && t != "managed") next
      if (n ~ /-ap$/ || n == "uap0" || n == "niloap0" || n == "ap0") {
        print n
        exit
      }
    }'
}

hostapd_bin() {
  if [[ -n "${HOSTAPD_BIN:-}" && -x "${HOSTAPD_BIN}" ]]; then
    echo "${HOSTAPD_BIN}"
    return
  fi
  command -v hostapd 2>/dev/null || echo hostapd
}

create_virtual_ap_iface() {
  local sta="$1" ap="$2" ap_mac="$3" prefer="${4:-__ap}"
  local phy
  phy="$(phy_for_sta "${sta}")"

  _try_create() {
    local typ="$1"
    if iw dev "${sta}" interface add "${ap}" type "${typ}" addr "${ap_mac}" 2>/dev/null; then
      log "Created virtual AP ${ap} (${typ}, mac=${ap_mac}) on ${sta}"
      return 0
    fi
    if [[ -n "${phy}" ]] && iw "${phy}" interface add "${ap}" type "${typ}" addr "${ap_mac}" 2>/dev/null; then
      log "Created virtual AP ${ap} (${typ}, mac=${ap_mac}) via ${phy}"
      return 0
    fi
    return 1
  }

  if [[ "${prefer}" == "managed" ]]; then
    _try_create managed && return 0
    _try_create __ap && return 0
  else
    _try_create __ap && return 0
    _try_create managed && return 0
  fi
  return 1
}

kill_all_hostapd() {
  if pgrep -x hostapd >/dev/null 2>&1; then
    warn "Deteniendo procesos hostapd huérfanos"
    pkill -x hostapd 2>/dev/null || true
    sleep 0.5
  fi
}

prepare_ap_for_hostapd() {
  local ap="$1"
  ip link set "${ap}" down 2>/dev/null || true
  ip addr flush dev "${ap}" 2>/dev/null || true
  sleep 0.3
}

kill_nilo_hostapd() {
  local hostapd_log="${RUNTIME_DIR}/hostapd.log"
  local pid_file="${PID_DIR}/hostapd.pid"
  if [[ -f "${pid_file}" ]]; then
    local pid
    pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [[ -n "${pid}" ]]; then
      kill "${pid}" 2>/dev/null || true
      sleep 0.2
      kill -9 "${pid}" 2>/dev/null || true
    fi
    rm -f "${pid_file}"
  fi
  pkill -f "${RUNTIME_DIR}/hostapd.conf" 2>/dev/null || true
  sleep 0.3
  if [[ -f "${hostapd_log}" ]]; then
    : > "${hostapd_log}"
  fi
}

start_hostapd_daemon() {
  local ap="${1:-}"
  local hostapd_log="${RUNTIME_DIR}/hostapd.log"
  local hostapd_conf="${RUNTIME_DIR}/hostapd.conf"
  local bin
  bin="$(hostapd_bin)"
  kill_nilo_hostapd
  kill_all_hostapd
  [[ -n "${ap}" ]] && prepare_ap_for_hostapd "${ap}"
  rm -f "${hostapd_log}"
  # hostapd -t provoca segfault en algunos builds iwlwifi — no usar
  log "Arrancando ${bin} ($(${bin} -v 2>&1 | head -1 || true))"
  set +e
  "${bin}" -B -P "${PID_DIR}/hostapd.pid" "${hostapd_conf}" >>"${hostapd_log}" 2>&1
  local rc=$?
  set -e
  if [[ "${rc}" -ne 0 ]]; then
    [[ -f "${hostapd_log}" ]] && tail -15 "${hostapd_log}" >&2 || true
  fi
  return "${rc}"
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
    case "${name}" in
      *-ap|uap0|niloap0|ap0) ;;
      *) continue ;;
    esac
    if [[ "${typ}" == "AP" || "${typ}" == "__ap" || "${typ}" == "managed" ]]; then
      log "Removing stale virtual iface ${name} (${typ})"
      ip link set "${name}" down 2>/dev/null || true
      iw dev "${name}" del 2>/dev/null || ip link del "${name}" 2>/dev/null || true
    fi
  done < <(iw dev 2>/dev/null | awk '
    $1=="Interface"{n=$2; t=""}
    $1=="type"{t=$2; if (n!="") print n, t}')
  for ghost in "$(resolve_ap_name "${sta}" "${WIFI_AP_INTERFACE}")" "${sta}-ap" niloap0 uap0; do
    ip link set "${ghost}" down 2>/dev/null || true
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
  local ap try_names created="" ap_mac create_pref="__ap"
  rfkill unblock wifi 2>/dev/null || true
  iw reg set "${WIFI_COUNTRY_CODE}" 2>/dev/null || true
  cleanup_phy_ap_ifaces "${sta}"

  ap_mac="$(derive_ap_mac "${sta}")" || { warn "Could not derive AP MAC from ${sta}"; return 1; }

  # 2.4 GHz: managed + hostapd funciona mejor en iwlwifi AP+STA
  local sta_ch
  sta_ch="$(detect_sta_channel "${sta}")"
  if [[ "${sta_ch}" -le 14 ]]; then
    create_pref="managed"
  fi
  [[ -n "${WIFI_AP_CREATE_TYPE:-}" ]] && create_pref="${WIFI_AP_CREATE_TYPE}"
  AP_CREATE_PREF="${create_pref}"

  try_names="$(resolve_ap_name "${sta}" "${ap_cfg}") ${sta}-ap niloap0 uap0"
  for ap in ${try_names}; do
    [[ "${ap}" == "${sta}" ]] && continue
    iw dev "${ap}" del 2>/dev/null || ip link del "${ap}" 2>/dev/null || true
    sleep 0.2
    if create_virtual_ap_iface "${sta}" "${ap}" "${ap_mac}" "${create_pref}"; then
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
    ip link set "${ap}" address "${ap_mac}" 2>/dev/null || true
    ip link set "${ap}" down 2>/dev/null || true
  fi
}

is_dfs_channel() {
  local ch="$1"
  [[ "${ch}" -le 14 ]] && return 1
  if [[ "${ch}" -ge 52 && "${ch}" -le 64 ]]; then return 0; fi
  if [[ "${ch}" -ge 100 && "${ch}" -le 140 ]]; then return 0; fi
  return 1
}

sta_link_freq_mhz() {
  local sta="$1"
  iw dev "${sta}" link 2>/dev/null | awk '/freq:/ { print int($2 + 0); exit }'
}

print_dfs_workaround() {
  local ch="$1"
  is_dfs_channel "${ch}" || return 0
  local freq
  freq="$(sta_link_freq_mhz "$(detect_sta_iface)")"
  warn "Canal ${ch} (${freq:+"~${freq} MHz "}5 GHz DFS). Este chipset no pasa CAC DFS (start_dfs_cac -1)."
  warn "Solución A (automática): pon WIFI_ROUTER_PASSWORD en ${ENV_FILE} y ejecuta:"
  warn "  sudo $0 up"
  warn "  (reconecta al BSSID 2.4 GHz y arranca el AP)"
  warn "Solución manual:"
  warn "  nmcli -t -f SSID,BSSID,FREQ dev wifi list | grep -i TU_SSID"
  warn "  sudo nmcli dev wifi connect \"SSID\" password \"CLAVE\" ifname wlp3s0 bssid XX:XX:..."
  warn "Solución B (Ethernet): cable + AP dedicado 2.4 GHz:"
  warn "  sudo WIFI_DEDICATED_AP=1 $0 up"
}

abort_if_sta_on_dfs() {
  local sta="$1" ch freq
  [[ "${WIFI_DEDICATED_AP:-0}" == "1" ]] && return 0
  [[ "${WIFI_ALLOW_DFS:-0}" == "1" ]] && return 0
  ch="$(detect_sta_channel "${sta}")"
  is_dfs_channel "${ch}" || return 0
  freq="$(sta_link_freq_mhz "${sta}")"
  warn "STA ${sta} en ${freq:+"${freq} MHz / "}canal ${ch} (5 GHz DFS) — AP+STA concurrente no viable aquí."
  print_dfs_workaround "${ch}"
  return 1
}

prepare_dedicated_ap() {
  local sta="$1"
  AP_MODE="dedicated"
  WIFI_AP_INTERFACE="${sta}"
  WIFI_CHANNEL="${WIFI_CHANNEL:-6}"
  rfkill unblock wifi 2>/dev/null || true
  iw reg set "${WIFI_COUNTRY_CODE}" 2>/dev/null || true
  cleanup_phy_ap_ifaces "${sta}"
  if command -v nmcli >/dev/null 2>&1; then
    nmcli device disconnect "${sta}" 2>/dev/null || true
    nmcli device set "${sta}" managed no 2>/dev/null || true
  fi
  log "AP dedicado en ${sta} canal ${WIFI_CHANNEL} (sin WiFi cliente concurrente)"
}

write_configs() {
  local ap="$1" ssid="$2" pass="$3" hw_mode="g"
  mkdir -p "${RUNTIME_DIR}" "${PID_DIR}"
  [[ "${WIFI_CHANNEL}" -gt 14 ]] && hw_mode="a"
  cat > "${RUNTIME_DIR}/hostapd.conf" <<EOF
interface=${ap}
driver=nl80211
ssid=${ssid}
channel=${WIFI_CHANNEL}
country_code=${WIFI_COUNTRY_CODE}
ieee80211d=1
hw_mode=${hw_mode}
ieee80211n=1
wmm_enabled=1
auth_algs=1
ignore_broadcast_ssid=0
EOF
  if [[ "${hw_mode}" == "a" ]]; then
    echo "ieee80211ac=1" >> "${RUNTIME_DIR}/hostapd.conf"
  fi
  if is_dfs_channel "${WIFI_CHANNEL}"; then
    echo "ieee80211h=1" >> "${RUNTIME_DIR}/hostapd.conf"
    log "Canal ${WIFI_CHANNEL} es DFS — ieee80211h=1 (CAC radar ~60s al arrancar)"
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

start_ap_core() {
  local sta ssid pass ap
  sta="$(detect_sta_iface)"
  [[ -n "${sta}" ]] || { warn "No WiFi STA interface"; return 1; }
  abort_if_sta_on_dfs "${sta}" || return 1
  ap="${WIFI_AP_INTERFACE}"
  ssid="$(build_ssid)"
  pass="${NILO_WIFI_PASSWORD:-}"
  WIFI_CHANNEL="$(detect_sta_channel "${sta}")"

  if [[ "${WIFI_DEDICATED_AP:-0}" == "1" ]]; then
    prepare_dedicated_ap "${sta}"
    ap="${WIFI_AP_INTERFACE}"
  else
    ensure_ap_iface "${sta}" "${ap}"
    ap="${WIFI_AP_INTERFACE}"
    if is_dfs_channel "${WIFI_CHANNEL}"; then
      log "STA en canal DFS ${WIFI_CHANNEL} — si el AP no arranca, usa WiFi 2.4 GHz o WIFI_DEDICATED_AP=1"
    fi
  fi
  write_configs "${ap}" "${ssid}" "${pass}"

  pkill -f "${RUNTIME_DIR}/dnsmasq.conf" 2>/dev/null || true
  sleep 0.2

  local hostapd_log="${RUNTIME_DIR}/hostapd.log"
  local hostapd_rc=0
  if start_hostapd_daemon "${ap}"; then
    hostapd_rc=0
  else
    hostapd_rc=$?
    if [[ "${hostapd_rc}" -eq 139 || "${hostapd_rc}" -gt 128 ]]; then
      warn "hostapd crash (rc=${hostapd_rc}) — recreando interfaz (${AP_CREATE_PREF}→alt) y reintentando"
      if [[ "${AP_MODE}" == "concurrent" && -n "${sta}" ]]; then
        cleanup_phy_ap_ifaces "${sta}"
        sleep 0.5
        if [[ "${AP_CREATE_PREF}" == "managed" ]]; then
          WIFI_AP_CREATE_TYPE="__ap"
        else
          WIFI_AP_CREATE_TYPE="managed"
        fi
        ensure_ap_iface "${sta}" "${WIFI_AP_INTERFACE}"
        ap="${WIFI_AP_INTERFACE}"
        write_configs "${ap}" "${ssid}" "${pass}"
      fi
      if start_hostapd_daemon "${ap}"; then
        hostapd_rc=0
      else
        hostapd_rc=$?
      fi
      unset WIFI_AP_CREATE_TYPE
    fi
  fi

  if [[ "${hostapd_rc}" -ne 0 ]] && ! pgrep -f "${RUNTIME_DIR}/hostapd.conf" >/dev/null 2>&1; then
    warn "hostapd falló al arrancar (rc=${hostapd_rc})"
    print_dfs_workaround "${WIFI_CHANNEL}"
    [[ -f "${hostapd_log}" ]] && tail -25 "${hostapd_log}" >&2 || true
    return 1
  fi

  if is_dfs_channel "${WIFI_CHANNEL}"; then
    log "Esperando CAC DFS en canal ${WIFI_CHANNEL} (hasta 90s)..."
    for _ in $(seq 1 90); do
      pgrep -f "${RUNTIME_DIR}/hostapd.conf" >/dev/null 2>&1 || break
      if iw dev "${ap}" info 2>/dev/null | grep -q 'type AP'; then
        break
      fi
      sleep 1
    done
  else
    sleep 1.0
  fi

  if ! pgrep -f "${RUNTIME_DIR}/hostapd.conf" >/dev/null 2>&1; then
    warn "hostapd terminó durante el arranque"
    print_dfs_workaround "${WIFI_CHANNEL}"
    [[ -f "${hostapd_log}" ]] && tail -25 "${hostapd_log}" >&2 || true
    return 1
  fi

  link_up_benign "${ap}" || true
  ip addr flush dev "${ap}" 2>/dev/null || true
  ip addr replace "${WIFI_AP_IP}/24" dev "${ap}" 2>/dev/null || true

  dnsmasq --conf-file="${RUNTIME_DIR}/dnsmasq.conf" --pid-file="${PID_DIR}/dnsmasq.pid" -k &
  sleep 0.5

  if iw dev "${ap}" info 2>/dev/null | grep -q 'type AP'; then
    log "AP active: ssid=${ssid} mode=${AP_MODE} sta=${sta} ap=${ap} ip=${WIFI_AP_IP}"
    return 0
  fi
  warn "hostapd running but ${ap} is not type AP"
  return 1
}

start_ap() { up_ap; }

stop_ap() {
  load_env
  local sta
  pkill -f "${RUNTIME_DIR}/dnsmasq.conf" 2>/dev/null || true
  kill_nilo_hostapd
  rm -f "${PID_DIR}/dnsmasq.pid"
  sta="$(detect_sta_iface)"
  if [[ -n "${sta}" ]]; then
    cleanup_phy_ap_ifaces "${sta}"
  fi
  log "AP stopped"
}

status_ap() {
  local sta ap expected ch
  sta="$(detect_sta_iface)"
  expected="$(resolve_ap_name "${sta}" "${WIFI_AP_INTERFACE}")"
  ap="$(detect_ap_iface "${sta}")"
  [[ -z "${ap}" ]] && ap="${expected}"
  ch="$(detect_sta_channel "${sta}")"
  freq="$(sta_link_freq_mhz "${sta}")"
  echo "Install dir:   ${INSTALL_DIR}"
  echo "Runtime dir:   ${RUNTIME_DIR}"
  echo "STA interface: ${sta:-?}"
  echo "AP interface:  ${ap:-?} (expected: ${expected})"
  if [[ -n "${freq}" ]]; then
    echo "STA link:      ${freq} MHz, canal ${ch}$(is_dfs_channel "${ch}" && echo ' (5 GHz DFS — usa WiFi 2.4 GHz o Ethernet)')"
  else
    echo "STA channel:   ${ch}"
  fi
  if [[ -n "${ap}" && "${ap}" != "auto" ]] && iw dev "${ap}" info 2>/dev/null; then
    echo ""
  fi
  if [[ -f "${RUNTIME_DIR}/hostapd.conf" ]]; then
    grep -E '^(interface|ssid|channel|hw_mode|ieee80211h)=' "${RUNTIME_DIR}/hostapd.conf" 2>/dev/null || true
  else
    echo "(no hostapd.conf yet — run: sudo $0 up)"
  fi
  pgrep -af 'hostapd|dnsmasq' 2>/dev/null | grep -E 'nilo|wifi-runtime' || echo "(no nilo hostapd/dnsmasq)"
  if ! pgrep -f "${RUNTIME_DIR}/hostapd.conf" >/dev/null 2>&1; then
    local hlog="${RUNTIME_DIR}/hostapd.log"
    if [[ -f "${hlog}" ]]; then
      echo "--- hostapd.log (últimas líneas) ---"
      tail -15 "${hlog}" 2>/dev/null || true
    fi
    if is_dfs_channel "${ch}"; then
      echo ""
      echo "⚠ STA en canal DFS ${ch}: ejecuta sudo $0 up (reconecta a 2.4 GHz y arranca AP),"
      echo "  o usa Ethernet + WIFI_DEDICATED_AP=1"
    fi
  fi
  curl -sf "http://${WIFI_AP_IP}:8080/api/v1/health" >/dev/null && echo "HTTP OK on ${WIFI_AP_IP}:8080" || echo "HTTP not reachable on ${WIFI_AP_IP}:8080"
}

load_env
case "${ACTION}" in
  up|restart|start) up_ap ;;
  stop) stop_ap ;;
  status) status_ap ;;
  check) check_ap ;;
  *)
    echo "Usage: sudo $0 up|check|status|stop" >&2
    echo "  up     — limpia, comprueba 2.4 GHz, reconecta si hace falta, arranca AP (recomendado)" >&2
    echo "  check  — solo comprueba si la STA es apta (2.4 GHz)" >&2
    echo "  status — estado actual" >&2
    echo "  stop   — parar AP" >&2
    exit 1
    ;;
esac
