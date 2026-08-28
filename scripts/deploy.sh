#!/usr/bin/env bash
# NILO-Node production deploy helper.
#
# Usage:
#   sudo ./scripts/deploy.sh install              # first-time setup
#   sudo ./scripts/deploy.sh update               # pull + rebuild + WiFi AP restart
#   sudo ./scripts/deploy.sh reload               # restart container + WiFi AP
#   sudo SKIP_WIFI_AP=1 ./scripts/deploy.sh update   # skip WiFi host/API steps
#   sudo NILO_DOCKER_PULL=1 ./scripts/deploy.sh update   # also refresh base images
#   ./scripts/deploy.sh status                    # health + container state
#   ./scripts/deploy.sh logs [-f]                 # compose logs
#   sudo ./scripts/deploy.sh stop                 # stop container
#   sudo ./scripts/deploy.sh uninstall            # stop (+ optional data wipe)
#
# Environment (optional):
#   NILO_INSTALL_DIR=/opt/nilo-node   # default when not run from a git clone
#   NILO_REPO=https://github.com/NeoVisions/NILO-Node.git
#   NILO_REPO_BRANCH=main
#   NILO_IMAGE=ghcr.io/org/nilo-node:latest   # skip build, pull from registry
#   DEPLOY_MODE=auto|git|image                  # default: auto
#   INSTALL_SYSTEMD=1                         # install systemd unit on install
#   NONINTERACTIVE=1                            # no prompts (generates secrets)
#
# Examples:
#   sudo NILO_IMAGE=ghcr.io/neovisions/nilo-node:latest ./scripts/deploy.sh install
#   curl -fsSL .../scripts/deploy.sh | sudo bash -s -- install

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -z "${NILO_INSTALL_DIR:-}" ]]; then
  if [[ -d "${SOURCE_REPO_ROOT}/.git" ]]; then
    NILO_INSTALL_DIR="${SOURCE_REPO_ROOT}"
  else
    NILO_INSTALL_DIR="/opt/nilo-node"
  fi
fi
NILO_REPO="${NILO_REPO:-https://github.com/NeoVisions/NILO-Node.git}"
NILO_REPO_BRANCH="${NILO_REPO_BRANCH:-main}"
NILO_IMAGE="${NILO_IMAGE:-}"
DEPLOY_MODE="${DEPLOY_MODE:-auto}"
NILO_DOCKER_PULL="${NILO_DOCKER_PULL:-0}"
INSTALL_SYSTEMD="${INSTALL_SYSTEMD:-0}"
NONINTERACTIVE="${NONINTERACTIVE:-0}"
API_PORT="${API_PORT:-8080}"
ENV_SECRETS_CHANGED=0

COMPOSE_FILE=""
COMPOSE_PROJECT="nilo-node"
DC=(docker compose -p "${COMPOSE_PROJECT}")

log() { printf '[nilo-deploy] %s\n' "$*"; }
warn() { printf '[nilo-deploy] WARN: %s\n' "$*" >&2; }
die() { printf '[nilo-deploy] ERROR: %s\n' "$*" >&2; exit 1; }

need_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "Run as root: sudo $0 $*"
  fi
}

command_exists() { command -v "$1" >/dev/null 2>&1; }

gen_secret() {
  if command_exists openssl; then
    openssl rand -hex 24
  else
    tr -dc 'a-f0-9' </dev/urandom | head -c 48
    echo
  fi
}

install_docker() {
  if command_exists docker; then
    log "Docker already installed: $(docker --version)"
    return 0
  fi

  log "Installing Docker Engine..."
  if command_exists apt-get; then
    apt-get update -qq
    apt-get install -y ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
      curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      chmod a+r /etc/apt/keyrings/docker.gpg
    fi
    local codename
    codename="$(. /etc/os-release && echo "${VERSION_CODENAME:-bookworm}")"
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
      ${codename} stable" >/etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  else
    curl -fsSL https://get.docker.com | sh
  fi

  systemctl enable --now docker
  log "Docker installed: $(docker --version)"
}

ensure_compose() {
  if docker compose version >/dev/null 2>&1; then
    return 0
  fi
  die "Docker Compose plugin not found. Install docker-compose-plugin."
}

detect_deploy_mode() {
  if [[ "${DEPLOY_MODE}" != "auto" ]]; then
    echo "${DEPLOY_MODE}"
    return
  fi
  if [[ -n "${NILO_IMAGE}" ]]; then
    echo "image"
  else
    echo "git"
  fi
}

sync_install_dir_from_git() {
  if [[ -d "${NILO_INSTALL_DIR}/.git" ]]; then
    log "Updating repo at ${NILO_INSTALL_DIR}..."
    git -C "${NILO_INSTALL_DIR}" fetch --depth 1 origin "${NILO_REPO_BRANCH}"
    git -C "${NILO_INSTALL_DIR}" checkout "${NILO_REPO_BRANCH}"
    git -C "${NILO_INSTALL_DIR}" pull --ff-only origin "${NILO_REPO_BRANCH}" || true
    return
  fi

  if [[ -f "${SOURCE_REPO_ROOT}/docker-compose.prod.yml" ]] \
    && [[ "${SOURCE_REPO_ROOT}" != "${NILO_INSTALL_DIR}" ]]; then
    log "Copying project to ${NILO_INSTALL_DIR}..."
    mkdir -p "${NILO_INSTALL_DIR}"
    rsync -a --delete \
      --exclude '.git' \
      --exclude '.venv' \
      --exclude '__pycache__' \
      --exclude 'config/nilo-node.yaml' \
      --exclude '.env' \
      "${SOURCE_REPO_ROOT}/" "${NILO_INSTALL_DIR}/"
    return
  fi

  log "Cloning ${NILO_REPO} → ${NILO_INSTALL_DIR}..."
  mkdir -p "$(dirname "${NILO_INSTALL_DIR}")"
  git clone --depth 1 --branch "${NILO_REPO_BRANCH}" "${NILO_REPO}" "${NILO_INSTALL_DIR}"
}

prepare_image_only_layout() {
  mkdir -p "${NILO_INSTALL_DIR}/config"
  local standalone="${SOURCE_REPO_ROOT}/deploy/compose.standalone.yml"
  if [[ ! -f "${standalone}" ]] && [[ -f "${NILO_INSTALL_DIR}/deploy/compose.standalone.yml" ]]; then
    standalone="${NILO_INSTALL_DIR}/deploy/compose.standalone.yml"
  fi
  [[ -f "${standalone}" ]] || die "compose.standalone.yml not found"
  cp "${standalone}" "${NILO_INSTALL_DIR}/docker-compose.prod.yml"
}

setup_compose_file() {
  local mode="$1"
  cd "${NILO_INSTALL_DIR}"
  if [[ "${mode}" == "image" ]]; then
    if [[ ! -f docker-compose.prod.yml ]] || ! grep -q 'NILO_IMAGE' docker-compose.prod.yml 2>/dev/null; then
      prepare_image_only_layout
    fi
    COMPOSE_FILE="docker-compose.prod.yml"
  else
    [[ -f docker-compose.prod.yml ]] || die "Missing docker-compose.prod.yml in ${NILO_INSTALL_DIR}"
    COMPOSE_FILE="docker-compose.prod.yml"
  fi
  log "Using compose file: ${NILO_INSTALL_DIR}/${COMPOSE_FILE} (mode=${mode})"
}

ensure_config() {
  local example="${NILO_INSTALL_DIR}/config/nilo-node.example.yaml"
  local config="${NILO_INSTALL_DIR}/config/nilo-node.yaml"
  mkdir -p "${NILO_INSTALL_DIR}/config"

  if [[ -d "${config}" ]]; then
    warn "${config} is a directory (Docker bind-mount artifact) — recreating as file"
    rm -rf "${config}"
  fi

  if [[ ! -f "${config}" ]]; then
    [[ -f "${example}" ]] || die "Missing ${example}"
    cp "${example}" "${config}"
    log "Created ${config} from example — review before production use"
  else
    log "Config exists: ${config}"
  fi
}

apply_poe_camera_config() {
  local config="${NILO_INSTALL_DIR}/config/nilo-node.yaml"
  local patch="${NILO_INSTALL_DIR}/scripts/patch_camera_poe.py"
  [[ -f "${config}" ]] || return 0
  [[ -f "${patch}" ]] || { warn "patch_camera_poe.py not found — skip PoE config patch"; return 0; }
  log "Applying PoE camera settings to ${config}..."
  python3 "${patch}" "${config}"
}

apply_wifi_ap_config() {
  local config="${NILO_INSTALL_DIR}/config/nilo-node.yaml"
  local patch="${NILO_INSTALL_DIR}/scripts/patch_wifi_ap.py"
  [[ -f "${config}" ]] || return 0
  [[ -f "${patch}" ]] || { warn "patch_wifi_ap.py not found — skip WiFi AP patch"; return 0; }
  if ! wifi_ap_backend >/dev/null 2>&1; then
    if python3 - "${config}" <<'PY' 2>/dev/null
import sys, yaml
wifi = (yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}).get("wifi") or {}
sys.exit(0 if wifi.get("hardware_ap") is False else 1)
PY
    then
      log "WiFi AP omitido (hardware_ap=false — seguro en portátiles de desarrollo)"
      return 0
    fi
  fi
  log "Applying WiFi AP settings to ${config}..."
  python3 "${patch}" "${config}"
}

ensure_wifi_ap_host() {
  wifi_ap_backend >/dev/null 2>&1 || return 0
  local script="${NILO_INSTALL_DIR}/scripts/wifi/ensure-wifi-ap.sh"
  if [[ ! -f "${script}" ]]; then
    script="${SOURCE_REPO_ROOT}/scripts/wifi/ensure-wifi-ap.sh"
  fi
  [[ -f "${script}" ]] || { warn "ensure-wifi-ap.sh not found — skip host WiFi setup"; return 0; }
  chmod +x "${script}" "${SOURCE_REPO_ROOT}/scripts/wifi/"*.sh 2>/dev/null || true
  log "Host WiFi AP prerequisites (uap0 unmanaged, NM)..."
  NILO_WIFI_ALLOW_HOST_SCRIPTS=1 NILO_INSTALL_DIR="${NILO_INSTALL_DIR}" bash "${script}" || warn "ensure-wifi-ap.sh failed (non-fatal)"
}

wifi_ap_backend() {
  # Prints backend (container|host|auto) or exits 1 if WiFi AP should not run on host.
  local config="${NILO_INSTALL_DIR}/config/nilo-node.yaml"
  [[ -f "${config}" ]] || return 1
  python3 - "${config}" <<'PY'
import sys
import yaml

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    wifi = (yaml.safe_load(fh) or {}).get("wifi") or {}

if not wifi.get("enabled", False):
    sys.exit(1)
if wifi.get("hardware_ap") is False:
    sys.exit(1)
print(wifi.get("backend", "container"))
PY
}

prepare_wifi_ap_interface() {
  if [[ "${SKIP_WIFI_AP:-0}" == "1" ]]; then
    log "SKIP_WIFI_AP=1 — omitiendo preparación de interfaz WiFi"
    return 0
  fi
  local backend
  backend="$(wifi_ap_backend 2>/dev/null)" || return 0

  local prep="${NILO_INSTALL_DIR}/scripts/wifi/prepare-ap-interface.sh"
  if [[ ! -f "${prep}" ]]; then
    prep="${SOURCE_REPO_ROOT}/scripts/wifi/prepare-ap-interface.sh"
  fi
  [[ -f "${prep}" ]] || { warn "prepare-ap-interface.sh not found — skip"; return 0; }
  chmod +x "${prep}" 2>/dev/null || true
  log "Preparando host para WiFi AP (rfkill, regdomain, NM)..."
  NILO_WIFI_ALLOW_HOST_SCRIPTS=1 bash "${prep}" || warn "prepare-ap-interface.sh failed (non-fatal)"
}

cleanup_stale_uap0() {
  # Solo cuando el contenedor NO está en marcha. Borrar uap0 con hostapd activo
  # deja la interfaz en estado inconsistente ("Name not unique on network").
  command -v iw >/dev/null 2>&1 || return 0
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx nilo-node; then
    return 0
  fi
  if iw dev uap0 info >/dev/null 2>&1; then
    log "Eliminando uap0 huérfano (contenedor parado)..."
    ip link set uap0 down 2>/dev/null || true
    iw dev uap0 del 2>/dev/null || true
    sleep 1
  fi
}

restart_wifi_ap() {
  if [[ "${SKIP_WIFI_AP:-0}" == "1" ]]; then
    log "SKIP_WIFI_AP=1 — omitiendo reinicio WiFi AP"
    return 0
  fi
  local backend
  backend="$(wifi_ap_backend 2>/dev/null)" || {
    log "WiFi AP deshabilitado en config — omitiendo reinicio"
    return 0
  }

  if [[ "${backend}" == "host" ]]; then
    local run="${NILO_INSTALL_DIR}/scripts/wifi/wifi-ap-run.sh"
    if [[ ! -f "${run}" ]]; then
      run="${SOURCE_REPO_ROOT}/scripts/wifi/wifi-ap-run.sh"
    fi
    [[ -f "${run}" ]] || { warn "wifi-ap-run.sh not found"; return 1; }
    log "Reiniciando WiFi AP (backend=host)..."
    NILO_WIFI_ALLOW_HOST_SCRIPTS=1 NILO_INSTALL_DIR="${NILO_INSTALL_DIR}" bash "${run}" restart \
      || warn "wifi-ap-run.sh restart failed"
    return 0
  fi

  local token
  token="$(resolve_api_token)"
  if [[ -z "${token}" ]]; then
    warn "NILO_LOCAL_API_TOKEN no definido — generando en .env y reintentando tras reload"
    ensure_env_secrets
    token="$(resolve_api_token)"
  fi
  if [[ -z "${token}" ]]; then
    warn "No se pudo obtener NILO_LOCAL_API_TOKEN — omitiendo reinicio WiFi vía API"
    return 1
  fi

  sleep 3

  log "Reiniciando WiFi AP vía API..."
  local http_code
  http_code="$(curl -sf -o /tmp/nilo-wifi-restart.json -w '%{http_code}' \
    -X POST \
    -H "Authorization: Bearer ${token}" \
    "http://127.0.0.1:${API_PORT}/api/v1/wifi/restart" 2>/dev/null || echo "000")"

  if [[ "${http_code}" == "200" ]]; then
    log "WiFi AP reiniciado correctamente"
    if command_exists python3; then
      python3 -m json.tool /tmp/nilo-wifi-restart.json 2>/dev/null | grep -E '"ssid"|"running"|"mock"|"ap_mode"|"ap_interface"|"error"' || true
    fi
    return 0
  fi

  if [[ "${http_code}" == "401" || "${http_code}" == "403" ]]; then
    warn "Token API rechazado (HTTP ${http_code}). Ejecuta de nuevo: sudo $0 update"
  fi
  warn "Reinicio WiFi AP falló (HTTP ${http_code}). Revisa: $0 logs | grep -iE 'wifi|hostapd'"
  return 1
}

print_wifi_summary() {
  local backend
  backend="$(wifi_ap_backend 2>/dev/null)" || return 0

  log "── WiFi AP ──"
  if curl -sf "http://127.0.0.1:${API_PORT}/api/v1/node/info" >/dev/null 2>&1; then
    local info ssid ap_mode error
    info="$(curl -sf "http://127.0.0.1:${API_PORT}/api/v1/node/info")"
    ssid="$(echo "${info}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('wifi',{}).get('ssid','?'))" 2>/dev/null || echo "?")"
    ap_mode="$(echo "${info}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('wifi',{}).get('ap_mode','?'))" 2>/dev/null || echo "?")"
    error="$(echo "${info}" | python3 -c "import sys,json; d=json.load(sys.stdin); w=d.get('wifi',{}); print(w.get('error') or '')" 2>/dev/null || echo "")"
    log "SSID:     ${ssid}"
    log "Modo:     ${ap_mode} (backend=${backend})"
    log "Portal:   http://192.168.50.1:${API_PORT}/setup/"
    if [[ -n "${error}" ]]; then
      warn "WiFi error: ${error}"
    fi
  else
    warn "API no responde — no se pudo leer estado WiFi"
  fi
}

ensure_env() {
  local env_file="${NILO_INSTALL_DIR}/.env"
  local example="${NILO_INSTALL_DIR}/deploy/env.example"

  if [[ -f "${env_file}" ]]; then
    log "Env file exists: ${env_file}"
    return
  fi

  if [[ -f "${example}" ]]; then
    cp "${example}" "${env_file}"
  else
    touch "${env_file}"
  fi

  local token wifi setup_user setup_pass
  token="$(gen_secret)"
  wifi="$(gen_secret)"
  setup_user="${NILO_SETUP_USERNAME:-admin}"
  setup_pass="$(gen_secret)"

  if [[ "${NONINTERACTIVE}" == "1" ]]; then
    sed -i "s/^NILO_LOCAL_API_TOKEN=.*/NILO_LOCAL_API_TOKEN=${token}/" "${env_file}"
    sed -i "s/^NILO_WIFI_PASSWORD=.*/NILO_WIFI_PASSWORD=${wifi}/" "${env_file}"
    if grep -q '^NILO_SETUP_USERNAME=' "${env_file}"; then
      sed -i "s/^NILO_SETUP_USERNAME=.*/NILO_SETUP_USERNAME=${setup_user}/" "${env_file}"
    else
      echo "NILO_SETUP_USERNAME=${setup_user}" >> "${env_file}"
    fi
    if grep -q '^NILO_SETUP_PASSWORD=' "${env_file}"; then
      sed -i "s/^NILO_SETUP_PASSWORD=.*/NILO_SETUP_PASSWORD=${setup_pass}/" "${env_file}"
    else
      echo "NILO_SETUP_PASSWORD=${setup_pass}" >> "${env_file}"
    fi
    log "Generated API token, WiFi password, setup user/password in ${env_file}"
  else
    warn "Edit ${env_file} and set at least:"
    warn "  NILO_LOCAL_API_TOKEN (e.g. ${token})"
    warn "  NILO_WIFI_PASSWORD   (e.g. ${wifi})"
    warn "  NILO_SETUP_USERNAME  (e.g. ${setup_user})"
    warn "  NILO_SETUP_PASSWORD  (e.g. ${setup_pass})"
    warn "  NILO_BACKEND_*       (when backend is configured)"
  fi
}

load_env() {
  local env_file="${NILO_INSTALL_DIR}/.env"
  if [[ -f "${env_file}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${env_file}"
    set +a
  fi
}

set_env_var() {
  local key="$1" val="$2" env_file="${NILO_INSTALL_DIR}/.env"
  mkdir -p "${NILO_INSTALL_DIR}"
  touch "${env_file}"
  if grep -q "^${key}=" "${env_file}" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "${env_file}"
  else
    echo "${key}=${val}" >> "${env_file}"
  fi
}

read_env_var() {
  local key="$1" env_file="${NILO_INSTALL_DIR}/.env"
  [[ -f "${env_file}" ]] || return 0
  grep -E "^${key}=" "${env_file}" 2>/dev/null | cut -d= -f2- || true
}

ensure_install_dir_env() {
  set_env_var "NILO_INSTALL_DIR" "${NILO_INSTALL_DIR}"
}

read_node_id() {
  local vol_path id
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx nilo-node; then
    id="$(docker exec nilo-node cat /data/node_id 2>/dev/null || true)"
    [[ -n "${id}" ]] && { printf '%s' "${id}"; return; }
  fi
  vol_path="$(docker volume inspect nilo-node_nilo-data -f '{{.Mountpoint}}' 2>/dev/null || true)"
  if [[ -n "${vol_path}" && -f "${vol_path}/node_id" ]]; then
    cat "${vol_path}/node_id"
    return
  fi
}

sync_setup_portal_credentials() {
  local node_id short_id wifi
  wifi="$(read_env_var NILO_WIFI_PASSWORD)"
  [[ -n "${wifi}" ]] || return 0
  node_id="$(read_node_id)"
  [[ -n "${node_id}" ]] || return 0
  short_id="${node_id//-/}"
  short_id="${short_id:0:8}"
  set_env_var "NILO_SETUP_USERNAME" "${short_id}"
  set_env_var "NILO_SETUP_PASSWORD" "${wifi}"
  ENV_SECRETS_CHANGED=1
  log "Portal /setup/: usuario=${short_id}, contraseña=NILO_WIFI_PASSWORD"
}

ensure_env_secrets() {
  ensure_env
  ensure_install_dir_env
  local token wifi setup_pass
  token="$(read_env_var NILO_LOCAL_API_TOKEN)"
  wifi="$(read_env_var NILO_WIFI_PASSWORD)"
  setup_pass="$(read_env_var NILO_SETUP_PASSWORD)"
  local changed=0

  if [[ -z "${token}" ]]; then
    token="$(gen_secret)"
    set_env_var "NILO_LOCAL_API_TOKEN" "${token}"
    changed=1
  fi
  if [[ -z "${wifi}" ]]; then
    wifi="$(gen_secret)"
    set_env_var "NILO_WIFI_PASSWORD" "${wifi}"
    changed=1
  fi
  if [[ -z "${setup_pass}" ]]; then
    setup_pass="$(read_env_var NILO_WIFI_PASSWORD)"
    [[ -n "${setup_pass}" ]] || setup_pass="$(gen_secret)"
    set_env_var "NILO_SETUP_PASSWORD" "${setup_pass}"
    changed=1
  fi
  if [[ "${changed}" == "1" ]]; then
    ENV_SECRETS_CHANGED=1
    log "Secrets generados/actualizados en ${NILO_INSTALL_DIR}/.env"
  fi
  sync_setup_portal_credentials
}

resolve_api_token() {
  local token=""
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx nilo-node; then
    token="$(docker exec nilo-node printenv NILO_LOCAL_API_TOKEN 2>/dev/null || true)"
  fi
  if [[ -z "${token}" ]]; then
    load_env
    token="${NILO_LOCAL_API_TOKEN:-}"
  fi
  if [[ -z "${token}" ]]; then
    token="$(read_env_var NILO_LOCAL_API_TOKEN)"
  fi
  printf '%s' "${token}"
}

compose_build() {
  local pull_flag=()
  if [[ "${NILO_DOCKER_PULL}" == "1" ]]; then
    pull_flag=(--pull)
    log "Docker build with --pull (refresh base images)"
  else
    log "Docker build using cache (set NILO_DOCKER_PULL=1 to refresh base images)"
  fi
  ${DC[@]} -f "${COMPOSE_FILE}" build "${pull_flag[@]}"
}

compose_up() {
  local mode="$1"
  cd "${NILO_INSTALL_DIR}"
  export NILO_IMAGE="${NILO_IMAGE:-}"
  local recreate=()
  if [[ "${ENV_SECRETS_CHANGED:-0}" == "1" ]]; then
    recreate=(--force-recreate)
    log "Secrets nuevos en .env — recreando contenedor"
  fi

  if [[ "${mode}" == "image" ]]; then
    [[ -n "${NILO_IMAGE}" ]] || die "NILO_IMAGE is required for image deploy mode"
    log "Pulling image ${NILO_IMAGE}..."
    ${DC[@]} -f "${COMPOSE_FILE}" pull
    ${DC[@]} -f "${COMPOSE_FILE}" up -d --remove-orphans "${recreate[@]}"
  else
    log "Building image nilo-node:local..."
    compose_build
    ${DC[@]} -f "${COMPOSE_FILE}" up -d --remove-orphans "${recreate[@]}"
  fi
}

compose_update() {
  local mode="$1"
  cd "${NILO_INSTALL_DIR}"
  export NILO_IMAGE="${NILO_IMAGE:-}"
  local recreate=()
  if [[ "${ENV_SECRETS_CHANGED:-0}" == "1" ]]; then
    recreate=(--force-recreate)
    log "Secrets nuevos en .env — recreando contenedor"
  fi

  if [[ "${mode}" == "image" ]]; then
    [[ -n "${NILO_IMAGE}" ]] || die "NILO_IMAGE is required for image deploy mode"
    ${DC[@]} -f "${COMPOSE_FILE}" pull
    ${DC[@]} -f "${COMPOSE_FILE}" up -d --remove-orphans "${recreate[@]}"
  else
    compose_build
    ${DC[@]} -f "${COMPOSE_FILE}" up -d --remove-orphans "${recreate[@]}"
  fi
}

compose_reload() {
  cd "${NILO_INSTALL_DIR}"
  log "Restarting container without rebuild..."
  ${DC[@]} -f "${COMPOSE_FILE}" up -d --no-build --remove-orphans
}

install_systemd_unit() {
  local unit_src="${NILO_INSTALL_DIR}/deploy/systemd/nilo-node.service"
  [[ -f "${unit_src}" ]] || { warn "systemd unit not found, skipping"; return; }

  sed "s|/opt/nilo-node|${NILO_INSTALL_DIR}|g" "${unit_src}" >/etc/systemd/system/nilo-node.service
  systemctl daemon-reload
  systemctl enable nilo-node.service
  log "systemd unit installed: nilo-node.service (enable/start with: systemctl start nilo-node)"
}

wait_healthy() {
  local retries=30
  local url="http://127.0.0.1:${API_PORT}/api/v1/health"
  log "Waiting for health at ${url} ..."
  for ((i = 1; i <= retries; i++)); do
    if curl -sf "${url}" >/dev/null 2>&1; then
      log "Health check OK"
      curl -sf "${url}"
      echo
      return 0
    fi
    sleep 2
  done
  warn "Health check timed out — check logs: $0 logs"
  return 1
}

cmd_install() {
  need_root install
  local mode
  mode="$(detect_deploy_mode)"
  log "Deploy mode: ${mode}"

  install_docker
  ensure_compose

  if [[ "${mode}" == "git" ]]; then
    sync_install_dir_from_git
  else
    mkdir -p "${NILO_INSTALL_DIR}/config"
    if [[ ! -f "${NILO_INSTALL_DIR}/config/nilo-node.example.yaml" ]]; then
      if [[ -f "${SOURCE_REPO_ROOT}/config/nilo-node.example.yaml" ]]; then
        cp "${SOURCE_REPO_ROOT}/config/nilo-node.example.yaml" "${NILO_INSTALL_DIR}/config/"
        cp "${SOURCE_REPO_ROOT}/deploy/env.example" "${NILO_INSTALL_DIR}/deploy/env.example" 2>/dev/null || true
        mkdir -p "${NILO_INSTALL_DIR}/deploy"
        cp "${SOURCE_REPO_ROOT}/deploy/compose.standalone.yml" "${NILO_INSTALL_DIR}/deploy/" 2>/dev/null || true
      else
        sync_install_dir_from_git
        mode="git"
      fi
    fi
  fi

  setup_compose_file "${mode}"
  ensure_config
  apply_poe_camera_config
  apply_wifi_ap_config
  ensure_wifi_ap_host
  prepare_wifi_ap_interface
  ensure_env_secrets
  load_env
  if wifi_ap_backend >/dev/null 2>&1; then
    cleanup_stale_uap0
  fi
  compose_up "${mode}"

  if [[ "${INSTALL_SYSTEMD}" == "1" ]]; then
    install_systemd_unit
  fi

  wait_healthy || health_ok=0
  if [[ "${health_ok:-1}" == "1" ]]; then
    restart_wifi_ap || true
  else
    warn "API no responde — omitiendo reinicio WiFi. Diagnóstico: sudo $0 logs"
  fi
  print_wifi_summary || true
  log "Install complete. Install dir: ${NILO_INSTALL_DIR}"
  log "API token in: ${NILO_INSTALL_DIR}/.env (NILO_LOCAL_API_TOKEN)"
  log "Verify: curl -H \"Authorization: Bearer \$TOKEN\" http://127.0.0.1:${API_PORT}/api/v1/node/info"
}

cmd_reload() {
  need_root reload
  local mode
  mode="$(detect_deploy_mode)"
  [[ -d "${NILO_INSTALL_DIR}" ]] || die "Not installed at ${NILO_INSTALL_DIR}"
  setup_compose_file "${mode}"
  apply_poe_camera_config
  apply_wifi_ap_config
  ensure_wifi_ap_host
  prepare_wifi_ap_interface
  ensure_env_secrets
  load_env
  compose_reload
  wait_healthy || health_ok=0
  if [[ "${health_ok:-1}" == "1" ]]; then
    restart_wifi_ap || true
  else
    warn "API no responde — omitiendo reinicio WiFi. Diagnóstico: sudo $0 logs"
  fi
  print_wifi_summary || true
  log "Reload complete (no image rebuild)."
}

cmd_update() {
  need_root update
  local mode
  mode="$(detect_deploy_mode)"

  install_docker
  ensure_compose

  if [[ "${mode}" == "git" ]]; then
    sync_install_dir_from_git
  fi

  setup_compose_file "${mode}"
  apply_poe_camera_config
  apply_wifi_ap_config
  ensure_wifi_ap_host
  prepare_wifi_ap_interface
  ensure_env_secrets
  load_env
  if wifi_ap_backend >/dev/null 2>&1; then
    cleanup_stale_uap0
  fi
  compose_update "${mode}"
  wait_healthy || health_ok=0
  if [[ "${health_ok:-1}" == "1" ]]; then
    restart_wifi_ap || true
  else
    warn "API no responde — omitiendo reinicio WiFi. Diagnóstico: sudo $0 logs"
  fi
  print_wifi_summary || true
  log "Update complete."
}

cmd_status() {
  local mode
  mode="$(detect_deploy_mode)"
  [[ -d "${NILO_INSTALL_DIR}" ]] || die "Not installed at ${NILO_INSTALL_DIR}"

  setup_compose_file "${mode}"
  cd "${NILO_INSTALL_DIR}"
  ${DC[@]} -f "${COMPOSE_FILE}" ps || true

  if curl -sf "http://127.0.0.1:${API_PORT}/api/v1/health" 2>/dev/null; then
    echo
    log "Health: OK"
    curl -sf "http://127.0.0.1:${API_PORT}/api/v1/node/info" | python3 -m json.tool 2>/dev/null || true
  else
    warn "Health endpoint not reachable on port ${API_PORT}"
  fi
}

cmd_logs() {
  local follow="${1:-}"
  local mode
  mode="$(detect_deploy_mode)"
  [[ -d "${NILO_INSTALL_DIR}" ]] || die "Not installed at ${NILO_INSTALL_DIR}"
  setup_compose_file "${mode}"
  cd "${NILO_INSTALL_DIR}"
  if [[ "${follow}" == "-f" ]]; then
    ${DC[@]} -f "${COMPOSE_FILE}" logs -f --tail=200
  else
    ${DC[@]} -f "${COMPOSE_FILE}" logs --tail=200
  fi
}

cmd_stop() {
  need_root stop
  local mode
  mode="$(detect_deploy_mode)"
  [[ -d "${NILO_INSTALL_DIR}" ]] || die "Not installed at ${NILO_INSTALL_DIR}"
  setup_compose_file "${mode}"
  cd "${NILO_INSTALL_DIR}"
  ${DC[@]} -f "${COMPOSE_FILE}" down
  log "Stopped."
}

cmd_uninstall() {
  need_root uninstall
  cmd_stop

  if [[ "${NONINTERACTIVE}" == "1" ]]; then
    REPLY=n
  else
    read -r -p "Remove Docker volume nilo-data (ALL recordings)? [y/N] " REPLY
  fi
  if [[ "${REPLY}" =~ ^[Yy]$ ]]; then
    docker volume rm "${COMPOSE_PROJECT}_nilo-data" 2>/dev/null || docker volume rm nilo-data 2>/dev/null || true
    log "Volume removed."
  fi

  if [[ "${NONINTERACTIVE}" == "1" ]]; then
    REPLY=n
  else
    read -r -p "Remove install directory ${NILO_INSTALL_DIR}? [y/N] " REPLY
  fi
  if [[ "${REPLY}" =~ ^[Yy]$ ]]; then
    rm -rf "${NILO_INSTALL_DIR}"
    log "Install directory removed."
  fi

  if [[ -f /etc/systemd/system/nilo-node.service ]]; then
    systemctl disable nilo-node.service 2>/dev/null || true
    rm -f /etc/systemd/system/nilo-node.service
    systemctl daemon-reload
  fi
  log "Uninstall complete."
}

usage() {
  sed -n '2,24p' "$0" | sed 's/^# \?//'
  exit "${1:-0}"
}

main() {
  local cmd="${1:-install}"
  shift || true

  case "${cmd}" in
    install) cmd_install ;;
    update) cmd_update ;;
    reload) cmd_reload ;;
    status) cmd_status ;;
    logs) cmd_logs "${1:-}" ;;
    stop) cmd_stop ;;
    uninstall) cmd_uninstall ;;
    -h|--help|help) usage 0 ;;
    *)
      die "Unknown command: ${cmd}. Use: install | update | reload | status | logs | stop | uninstall"
      ;;
  esac
}

main "$@"
