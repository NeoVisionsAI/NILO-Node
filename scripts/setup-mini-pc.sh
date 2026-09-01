#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  NILO-Node — UN SOLO SCRIPT para el mini PC (instalar o actualizar todo)
#
#  Usage (desde el repo clonado, p. ej. ~/dev/NILO-Node):
#    sudo ./scripts/setup-mini-pc.sh
#
#  Hace todo en orden:
#    • Paquetes de sistema (Docker, hostapd, dnsmasq, Bluetooth, FFmpeg…) — solo si faltan
#    • Credenciales .env (menú interactivo la 1ª vez; Enter/10s = mantener)
#    • Docker + contenedor NILO-Node — install la 1ª vez, update si ya existe
#    • WiFi AP + portal /setup/ — limpieza, 2.4 GHz, arranque
#    • systemd (arranque automático al reiniciar el PC) — habilitado por defecto
#    • Red PoE (opcional, menú) — se omite si ya está configurada
#
#  Opciones:
#    sudo ./scripts/setup-mini-pc.sh --list-interfaces
#    sudo SKIP_POE=1 ./scripts/setup-mini-pc.sh
#    sudo POE_IFACE=enp2s0 ./scripts/setup-mini-pc.sh
#    sudo NONINTERACTIVE=1 ./scripts/setup-mini-pc.sh
#    sudo FORCE_APT=1 ./scripts/setup-mini-pc.sh      # reinstalar paquetes apt
#    sudo FORCE_POE=1 ./scripts/setup-mini-pc.sh      # reconfigurar PoE
#    sudo INSTALL_SYSTEMD=0 ./scripts/setup-mini-pc.sh   # sin servicio systemd
#
#  Tras ejecutar: WiFi nilo-node-XXXXXXXX → http://192.168.50.1:8080/setup/
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -z "${NILO_INSTALL_DIR:-}" ]]; then
  if [[ -d "${REPO_ROOT}/.git" ]]; then
    NILO_INSTALL_DIR="${REPO_ROOT}"
  else
    NILO_INSTALL_DIR="/opt/nilo-node"
  fi
fi

POE_IFACE="${POE_IFACE:-}"
SKIP_POE="${SKIP_POE:-0}"
NONINTERACTIVE="${NONINTERACTIVE:-0}"
INSTALL_SYSTEMD="${INSTALL_SYSTEMD:-1}"
FORCE_APT="${FORCE_APT:-0}"
FORCE_POE="${FORCE_POE:-0}"
POE_STATE_FILE="${POE_STATE_FILE:-${NILO_INSTALL_DIR}/config/poe.env}"

log() { printf '[nilo-setup] %s\n' "$*"; }
warn() { printf '[nilo-setup] WARN: %s\n' "$*" >&2; }
die() { printf '[nilo-setup] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "Ejecuta como root: sudo $0"

system_packages_ready() {
  command -v docker >/dev/null 2>&1 \
    && command -v hostapd >/dev/null 2>&1 \
    && command -v dnsmasq >/dev/null 2>&1 \
    && command -v iw >/dev/null 2>&1 \
    && command -v ffmpeg >/dev/null 2>&1 \
    && command -v nmcli >/dev/null 2>&1
}

is_nilo_installed() {
  [[ -f "${NILO_INSTALL_DIR}/.env" ]] \
    && [[ -f "${NILO_INSTALL_DIR}/config/nilo-node.yaml" ]] \
    && docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx nilo-node
}

install_apt_packages() {
  if [[ "${FORCE_APT}" != "1" ]] && system_packages_ready; then
    log "Paquetes de sistema OK — omitiendo apt (FORCE_APT=1 para reinstalar)"
    return 0
  fi

  log "Instalando paquetes del sistema..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq

  local required=(
    ca-certificates curl gnupg git
    hostapd dnsmasq iproute2 iw
    bluez
    ffmpeg
    network-manager
    python3 python3-pip
    jq
  )
  local optional=(
    wireless-tools
    bluez-tools
  )

  apt-get install -y "${required[@]}"

  for pkg in "${optional[@]}"; do
    if apt-cache show "${pkg}" >/dev/null 2>&1; then
      apt-get install -y "${pkg}" || warn "No se pudo instalar ${pkg} (opcional)"
    else
      warn "Paquete opcional no disponible: ${pkg} (omitido)"
    fi
  done

  log "Paquetes instalados."

  if systemctl is-enabled hostapd >/dev/null 2>&1; then
    systemctl stop hostapd 2>/dev/null || true
    systemctl disable hostapd 2>/dev/null || true
    systemctl mask hostapd 2>/dev/null || true
    log "hostapd systemd desactivado (NILO-Node gestiona el AP)."
  fi
}

write_credentials_file() {
  local env_file="${NILO_INSTALL_DIR}/.env"
  [[ -f "${env_file}" ]] || return 0

  local api_token wifi_pass setup_user setup_pass
  api_token="$(grep -E '^NILO_LOCAL_API_TOKEN=' "${env_file}" | cut -d= -f2- || true)"
  wifi_pass="$(grep -E '^NILO_WIFI_PASSWORD=' "${env_file}" | cut -d= -f2- || true)"
  setup_user="$(grep -E '^NILO_SETUP_USERNAME=' "${env_file}" | cut -d= -f2- || true)"
  setup_pass="$(grep -E '^NILO_SETUP_PASSWORD=' "${env_file}" | cut -d= -f2- || true)"

  CREDENTIALS_FILE="${NILO_INSTALL_DIR}/setup-credentials.txt"
  cat > "${CREDENTIALS_FILE}" <<EOF
# NILO-Node — credenciales $(date -Iseconds)
# Portal: http://192.168.50.1:8080/setup/

WiFi SSID:     nilo-node-XXXXXXXX (ver API tras arrancar)
WiFi password: ${wifi_pass}

Portal usuario: ${setup_user:-(uuid8 del nodo)}
Portal password: ${setup_pass}

API Bearer token: ${api_token}
EOF
  chmod 600 "${CREDENTIALS_FILE}"
  log "Credenciales en ${CREDENTIALS_FILE}"
}

deploy_nilo_node() {
  export NONINTERACTIVE INSTALL_SYSTEMD NILO_INSTALL_DIR REPO_ROOT
  if is_nilo_installed; then
    log "NILO-Node ya instalado en ${NILO_INSTALL_DIR} — actualizando..."
    "${REPO_ROOT}/scripts/deploy.sh" update
  else
    log "Primera instalación en ${NILO_INSTALL_DIR}..."
    "${REPO_ROOT}/scripts/deploy.sh" install
  fi
}

ensure_wifi_ap() {
  local run="${NILO_INSTALL_DIR}/scripts/wifi/wifi-ap-run.sh"
  [[ -f "${run}" ]] || run="${REPO_ROOT}/scripts/wifi/wifi-ap-run.sh"
  [[ -f "${run}" ]] || { warn "wifi-ap-run.sh no encontrado — omitiendo AP"; return 0; }
  log "WiFi AP (limpieza + 2.4 GHz + arranque)..."
  NILO_INSTALL_DIR="${NILO_INSTALL_DIR}" bash "${run}" up \
    || warn "wifi-ap-run.sh up falló — revisa: sudo ${run} status"
}

optional_poe_network() {
  if [[ "${SKIP_POE}" == "1" ]]; then
    log "SKIP_POE=1 — omitiendo red PoE."
    return 0
  fi

  if [[ "${FORCE_POE}" != "1" && -f "${POE_STATE_FILE}" ]]; then
    log "PoE ya configurado (${POE_STATE_FILE}) — omitiendo (FORCE_POE=1 para reconfigurar)"
    return 0
  fi

  local picker="${REPO_ROOT}/scripts/oak/network-interfaces.sh"
  [[ -x "${picker}" ]] || chmod +x "${picker}" 2>/dev/null || true
  [[ -x "${picker}" ]] || { warn "network-interfaces.sh no encontrado — omitiendo PoE"; return 0; }

  if [[ -z "${POE_IFACE}" ]]; then
    log "Red PoE (opcional)..."
    if POE_IFACE="$(POE_STATE_FILE="${POE_STATE_FILE}" "${picker}" pick)"; then
      log "Interfaz PoE: ${POE_IFACE}"
    else
      warn "PoE omitido (sin selección en menú)."
      return 0
    fi
  fi

  log "Configurando PoE en ${POE_IFACE}..."
  POE_IFACE="${POE_IFACE}" POE_STATE_FILE="${POE_STATE_FILE}" \
    "${REPO_ROOT}/scripts/oak/setup-poe-network.sh"
}

cmd_list_interfaces() {
  local picker="${REPO_ROOT}/scripts/oak/network-interfaces.sh"
  chmod +x "${picker}" 2>/dev/null || true
  POE_STATE_FILE="${POE_STATE_FILE}" "${picker}" list
}

print_summary() {
  log "════════════════════════════════════════════"
  log "NILO-Node listo — install dir: ${NILO_INSTALL_DIR}"
  if is_nilo_installed; then
    log "Estado:     instalado y actualizado"
  fi
  if curl -sf "http://127.0.0.1:8080/api/v1/health" >/dev/null 2>&1; then
    local info ssid node_short
    info="$(curl -sf "http://127.0.0.1:8080/api/v1/node/info" 2>/dev/null || echo '{}')"
    ssid="$(echo "${info}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('wifi',{}).get('ssid','?'))" 2>/dev/null || echo "?")"
    node_short="$(echo "${info}" | python3 -c "import sys,json; d=json.load(sys.stdin); n=d.get('node_id',''); print(n.replace('-','')[:8])" 2>/dev/null || echo "?")"
    log "WiFi SSID:  ${ssid}"
    log "Portal:     http://192.168.50.1:8080/setup/"
    log "Usuario:    ${node_short} (uuid8 del nodo)"
  fi
  local cred="${NILO_INSTALL_DIR}/setup-credentials.txt"
  [[ -f "${cred}" ]] && grep -E '^(WiFi|Portal)' "${cred}" 2>/dev/null || true
  log "════════════════════════════════════════════"
  log "Comando único para repetir todo: sudo $0"
}

main() {
  case "${1:-}" in
    --list-interfaces) cmd_list_interfaces; exit 0 ;;
    -h|--help)
      sed -n '3,24p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
  esac

  log "NILO-Node setup — ${NILO_INSTALL_DIR}"
  if is_nilo_installed; then
    log "Detectado: ya instalado → se omiten pasos completados"
  else
    log "Detectado: primera instalación"
  fi
  echo ""

  install_apt_packages
  optional_poe_network
  deploy_nilo_node
  ensure_wifi_ap
  write_credentials_file
  print_summary
}

main "$@"
