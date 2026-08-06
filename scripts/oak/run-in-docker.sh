#!/usr/bin/env bash
# Run OAK test scripts inside the hardware Docker image (no host Python venv).
#
# Usage:
#   ./scripts/oak/run-in-docker.sh tof
#   ./scripts/oak/run-in-docker.sh pose
#   ./scripts/oak/run-in-docker.sh model prepare --backend mediapipe
#   ./scripts/oak/run-in-docker.sh model prepare --backend yolo --weights ./yolov8n-pose.pt
#   ./scripts/oak/run-in-docker.sh build
#   ./scripts/oak/run-in-docker.sh shell
#
# Requires: Docker, display on host (X11/Wayland with xhost), OAK on USB.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose -f "${REPO_ROOT}/docker-compose.hardware.yml")

log() { printf '[oak-docker] %s\n' "$*"; }

enable_display() {
  export DISPLAY="${DISPLAY:-:0}"
  if command -v xhost >/dev/null 2>&1; then
    xhost +local:docker >/dev/null 2>&1 || xhost +SI:localuser:"$(whoami)" >/dev/null 2>&1 || true
  fi
  log "DISPLAY=${DISPLAY}"
}

build_image() {
  log "Building nilo-node:hardware image..."
  "${COMPOSE[@]}" build oak-test
}

run_oak_test() {
  local script_path="$1"
  shift
  enable_display
  "${COMPOSE[@]}" run --rm oak-test "${script_path}" "$@"
}

cmd="${1:-tof}"
shift || true

case "${cmd}" in
  build)
    build_image
    ;;
  tof)
    build_image
    run_oak_test /app/scripts/oak/tof_viewer.py "$@"
    ;;
  pose)
    build_image
    run_oak_test /app/scripts/oak/pose_viewer.py "$@"
    ;;
  model|toolchain)
    build_image
    run_oak_test /app/scripts/oak/model_toolchain.py "$@"
    ;;
  shell)
    build_image
    enable_display
    "${COMPOSE[@]}" run --rm --entrypoint bash oak-test
    ;;
  up)
    build_image
    cd "${REPO_ROOT}"
    docker compose -f docker-compose.hardware.yml up -d nilo-node-hw
    log "NILO-Node (hardware image) started — curl http://127.0.0.1:8080/api/v1/health"
    ;;
  *)
    echo "Usage: $0 {tof|pose|model|build|shell|up} [args...]" >&2
    exit 1
    ;;
esac
