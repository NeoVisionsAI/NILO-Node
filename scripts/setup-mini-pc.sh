#!/usr/bin/env bash
# Bootstrap completo del mini PC NILO-Node:
#   - Dependencias de sistema (Docker, WiFi AP, Bluetooth, FFmpeg, red PoE)
#   - Instalación del contenedor NILO-Node
#   - Credenciales de portal web (usuario/contraseña en .env)
#   - Red WiFi del nodo + acceso al portal /setup/
#
# Usage (desde el repo clonado):
#   sudo ./scripts/setup-mini-pc.sh                    # menú interactivo PoE
#   sudo ./scripts/setup-mini-pc.sh --list-interfaces  # solo listar redes
#   sudo POE_IFACE=enp2s0 ./scripts/setup-mini-pc.sh   # PoE sin menú
#   sudo SKIP_POE=1 ./scripts/setup-mini-pc.sh         # sin configurar PoE
#   sudo NONINTERACTIVE=1 ./scripts/setup-mini-pc.sh   # sin menús (auto/secrets)
#
# Tras ejecutar: conéctate al WiFi impreso y abre http://192.168.50.1:8080/setup/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
NILO_INSTALL_DIR="${NILO_INSTALL_DIR:-/opt/nilo-node}"
POE_IFACE="${POE_IFACE:-}"
SKIP_POE="${SKIP_POE:-0}"
NONINTERACTIVE="${NONINTERACTIVE:-0}"
INSTALL_SYSTEMD="${INSTALL_SYSTEMD:-0}"
POE_STATE_FILE="${POE_STATE_FILE:-${NILO_INSTALL_DIR}/config/poe.env}"

log() { printf '[nilo-setup] %s\n' "$*"; }
warn() { printf '[nilo-setup] WARN: %s\n' "$*" >&2; }
die() { printf '[nilo-setup] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "Ejecuta como root: sudo $0"

gen_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 16
  else
    tr -dc 'a-f0-9' </dev/urandom | head -c 32
    echo
  fi
}

install_apt_packages() {
  log "Instalando paquetes del sistema..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y \
    ca-certificates curl gnupg git \
    hostapd dnsmasq iproute2 wireless-tools iw \
    bluez bluez-tools \
    ffmpeg \
    network-manager \
    python3 python3-pip \
    jq
  log "Paquetes instalados."

  # Debian/Ubuntu suelen enmascarar hostapd — lo habilitamos para AP manual
  if systemctl is-enabled hostapd >/dev/null 2>&1; then
    systemctl stop hostapd 2>/dev/null || true
    systemctl disable hostapd 2>/dev/null || true
    systemctl mask hostapd 2>/dev/null || true
    log "hostapd systemd desactivado (NILO-Node gestiona el AP en el contenedor)."
  fi
}

prepare_env_credentials() {
  local env_file="${NILO_INSTALL_DIR}/.env"
  mkdir -p "${NILO_INSTALL_DIR}/deploy"

  if [[ ! -f "${env_file}" ]]; then
    if [[ -f "${REPO_ROOT}/deploy/env.example" ]]; then
      cp "${REPO_ROOT}/deploy/env.example" "${env_file}"
    else
      touch "${env_file}"
    fi
  fi

  local api_token wifi_pass setup_user setup_pass mqtt_user mqtt_pass
  api_token="$(grep -E '^NILO_LOCAL_API_TOKEN=' "${env_file}" | cut -d= -f2- || true)"
  wifi_pass="$(grep -E '^NILO_WIFI_PASSWORD=' "${env_file}" | cut -d= -f2- || true)"
  setup_user="$(grep -E '^NILO_SETUP_USERNAME=' "${env_file}" | cut -d= -f2- || true)"
  setup_pass="$(grep -E '^NILO_SETUP_PASSWORD=' "${env_file}" | cut -d= -f2- || true)"

  [[ -n "${api_token}" ]] || api_token="$(gen_secret)"
  [[ -n "${wifi_pass}" ]] || wifi_pass="$(gen_secret)"
  [[ -n "${setup_user}" ]] || setup_user="admin"
  [[ -n "${setup_pass}" ]] || setup_pass="$(gen_secret)"
  [[ -n "${mqtt_user}" ]] || mqtt_user=""
  [[ -n "${mqtt_pass}" ]] || mqtt_pass=""

  set_env_var() {
    local key="$1" val="$2"
    if grep -q "^${key}=" "${env_file}" 2>/dev/null; then
      sed -i "s|^${key}=.*|${key}=${val}|" "${env_file}"
    else
      echo "${key}=${val}" >> "${env_file}"
    fi
  }

  set_env_var "NILO_LOCAL_API_TOKEN" "${api_token}"
  set_env_var "NILO_WIFI_PASSWORD" "${wifi_pass}"
  set_env_var "NILO_SETUP_USERNAME" "${setup_user}"
  set_env_var "NILO_SETUP_PASSWORD" "${setup_pass}"

  CREDENTIALS_FILE="${NILO_INSTALL_DIR}/setup-credentials.txt"
  cat > "${CREDENTIALS_FILE}" <<EOF
# NILO-Node — credenciales generadas $(date -Iseconds)
# Portal web: http://192.168.50.1:8080/setup/

WiFi SSID:     (ver abajo tras arrancar — nilo-node-XXXXXXXX)
WiFi password: ${wifi_pass}

Portal usuario: ${setup_user}
Portal password: ${setup_pass}

API Bearer token (MQTT token field): ${api_token}

MQTT (configurar en portal o .env):
  NILO_MQTT_USERNAME=
  NILO_MQTT_PASSWORD=
EOF
  chmod 600 "${CREDENTIALS_FILE}"
  log "Credenciales guardadas en ${CREDENTIALS_FILE}"
}

install_nilo_node() {
  log "Instalando NILO-Node (Docker + contenedor)..."
  export NONINTERACTIVE="${NONINTERACTIVE}"
  export INSTALL_SYSTEMD="${INSTALL_SYSTEMD}"
  export NILO_INSTALL_DIR
  "${REPO_ROOT}/scripts/deploy.sh" install
}

optional_poe_network() {
  if [[ "${SKIP_POE}" == "1" ]]; then
    log "SKIP_POE=1 — omitiendo red PoE."
    return 0
  fi

  local picker="${REPO_ROOT}/scripts/oak/network-interfaces.sh"
  [[ -x "${picker}" ]] || chmod +x "${picker}"

  if [[ -z "${POE_IFACE}" ]]; then
    log "Detectando interfaces de red (menú PoE)..."
    if POE_IFACE="$(POE_STATE_FILE="${POE_STATE_FILE}" "${picker}" pick)"; then
      log "Interfaz PoE seleccionada: ${POE_IFACE}"
    else
      warn "PoE no configurado (omitido en el menú o sin interfaces)."
      return 0
    fi
  fi

  log "Configurando red PoE en ${POE_IFACE}..."
  POE_IFACE="${POE_IFACE}" POE_STATE_FILE="${POE_STATE_FILE}" \
    "${REPO_ROOT}/scripts/oak/setup-poe-network.sh"
}

cmd_list_interfaces() {
  local picker="${REPO_ROOT}/scripts/oak/network-interfaces.sh"
  chmod +x "${picker}" 2>/dev/null || true
  POE_STATE_FILE="${POE_STATE_FILE}" "${picker}" list
  if [[ -f "${POE_STATE_FILE}" ]]; then
    log "Guardado: ${POE_STATE_FILE}"
    cat "${POE_STATE_FILE}" >&2
  fi
}

print_summary() {
  local cred="${NILO_INSTALL_DIR}/setup-credentials.txt"
  log "============================================"
  log "NILO-Node listo."
  log ""
  if curl -sf "http://127.0.0.1:8080/api/v1/health" >/dev/null 2>&1; then
    local info ssid
    info="$(curl -sf "http://127.0.0.1:8080/api/v1/node/info" 2>/dev/null || echo '{}')"
    ssid="$(echo "${info}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('wifi',{}).get('ssid','?'))" 2>/dev/null || echo "?")"
    log "WiFi SSID: ${ssid}"
  fi
  log "Portal:    http://192.168.50.1:8080/setup/"
  log "Credenciales: ${cred}"
  log ""
  log "1) Conéctate al WiFi del nodo"
  log "2) Abre el portal e inicia sesión"
  log "3) Configura cámara / Bluetooth y pulsa Guardar"
  log "============================================"
}

main() {
  case "${1:-}" in
    --list-interfaces|-l)
      cmd_list_interfaces
      exit 0
      ;;
    --help|-h)
      sed -n '1,20p' "$0" >&2
      exit 0
      ;;
  esac

  log "Repo: ${REPO_ROOT}"
  install_apt_packages
  install_nilo_node
  prepare_env_credentials
  optional_poe_network

  log "Recargando servicio para aplicar .env..."
  NONINTERACTIVE=1 "${REPO_ROOT}/scripts/deploy.sh" reload || warn "Reload falló — revisa logs"

  print_summary
  if [[ -n "${POE_IFACE:-}" ]]; then
    log "PoE: ping -c 2 169.254.1.222"
  fi
}

main "$@"
