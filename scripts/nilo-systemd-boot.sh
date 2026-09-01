#!/usr/bin/env bash
# Boot hook for systemd: start NILO-Node stack and WiFi AP.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NILO_INSTALL_DIR="${NILO_INSTALL_DIR:-${SCRIPT_DIR}/..}"
API_PORT="${API_PORT:-8080}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-nilo-node}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

log() { printf '[nilo-boot] %s\n' "$*"; }
warn() { printf '[nilo-boot] WARN: %s\n' "$*" >&2; }

cd "${NILO_INSTALL_DIR}"

if ! command -v docker >/dev/null 2>&1; then
  warn "Docker no disponible — abortando arranque NILO-Node"
  exit 1
fi

log "Arrancando contenedor NILO-Node (${COMPOSE_FILE})..."
docker compose -p "${COMPOSE_PROJECT}" -f "${COMPOSE_FILE}" up -d --remove-orphans

run="${NILO_INSTALL_DIR}/scripts/wifi/wifi-ap-run.sh"
if [[ -f "${run}" ]]; then
  log "Esperando API en :${API_PORT}..."
  local_i=0
  while ((local_i < 45)); do
    if curl -sf "http://127.0.0.1:${API_PORT}/api/v1/health" >/dev/null 2>&1; then
      break
    fi
    local_i=$((local_i + 1))
    sleep 2
  done
  log "Arrancando WiFi AP..."
  NILO_INSTALL_DIR="${NILO_INSTALL_DIR}" bash "${run}" up || warn "wifi-ap-run.sh up falló"
fi

log "NILO-Node arrancado."
