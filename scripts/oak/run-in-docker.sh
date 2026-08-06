#!/usr/bin/env bash
# Run OAK test scripts inside the hardware Docker image (no host Python venv).
#
# Usage:
#   ./scripts/oak/run-in-docker.sh tof
#   ./scripts/oak/run-in-docker.sh discover
#   ./scripts/oak/run-in-docker.sh build          # force image rebuild
#   OAK_FORCE_BUILD=1 ./scripts/oak/run-in-docker.sh tof
#
# Scripts and src/ are bind-mounted — git pull updates code without rebuilding
# (unless pyproject.toml / Dockerfile changed). Set OAK_FORCE_BUILD=1 to rebuild.
#
# Requires: Docker, display (X11), OAK on PoE or USB (see docs/POE_SETUP.md)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose -f "${REPO_ROOT}/docker-compose.hardware.yml")
IMAGE="${OAK_DOCKER_IMAGE:-nilo-node:hardware}"

log() { printf '[oak-docker] %s\n' "$*"; }

enable_display() {
  export DISPLAY="${DISPLAY:-:0}"
  if command -v xhost >/dev/null 2>&1; then
    xhost +local:docker >/dev/null 2>&1 || xhost +SI:localuser:"$(whoami)" >/dev/null 2>&1 || true
  fi
  log "DISPLAY=${DISPLAY}"
}

build_image() {
  log "Building ${IMAGE} (heavy deps cached unless pyproject.toml changed)..."
  "${COMPOSE[@]}" build oak-test
}

ensure_image() {
  if [[ "${OAK_FORCE_BUILD:-0}" == "1" ]]; then
    build_image
    return
  fi
  if docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    log "Using existing ${IMAGE} — code from bind-mount (git pull is enough)"
    log "Rebuild only if needed: ./scripts/oak/run-in-docker.sh build"
    return
  fi
  log "Image ${IMAGE} not found — first build..."
  build_image
}

run_oak_test() {
  local script_path="$1"
  shift
  enable_display
  OAK_DEVICE_IP="${OAK_DEVICE_IP:-}" \
  OAK_DEVICE_ID="${OAK_DEVICE_ID:-}" \
  OAK_CONNECTION="${OAK_CONNECTION:-}" \
    "${COMPOSE[@]}" run --rm oak-test "${script_path}" "$@"
}

cmd="${1:-tof}"
shift || true

case "${cmd}" in
  build)
    build_image
    ;;
  discover)
    ensure_image
    OAK_CONNECTION="${OAK_CONNECTION:-}" \
      "${COMPOSE[@]}" run --rm oak-test /app/scripts/oak/discover_devices.py "$@"
    ;;
  tof)
    ensure_image
    run_oak_test /app/scripts/oak/tof_viewer.py "$@"
    ;;
  pose)
    ensure_image
    run_oak_test /app/scripts/oak/pose_viewer.py "$@"
    ;;
  model|toolchain)
    ensure_image
    run_oak_test /app/scripts/oak/model_toolchain.py "$@"
    ;;
  shell)
    ensure_image
    enable_display
    "${COMPOSE[@]}" run --rm --entrypoint bash oak-test
    ;;
  up)
    ensure_image
    cd "${REPO_ROOT}"
    docker compose -f docker-compose.hardware.yml up -d nilo-node-hw
    log "NILO-Node (hardware image) started — curl http://127.0.0.1:8080/api/v1/health"
    ;;
  *)
    echo "Usage: $0 {discover|tof|pose|model|build|shell|up} [args...]" >&2
    exit 1
    ;;
esac
