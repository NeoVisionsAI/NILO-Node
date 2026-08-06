#!/usr/bin/env bash
# Deprecated: use Docker hardware image instead.
#   ./scripts/oak/run-in-docker.sh tof
echo "Use: ./scripts/oak/run-in-docker.sh tof   (no host Python venv needed)" >&2
exec "$(dirname "$0")/run-in-docker.sh" "${1:-tof}" "${@:2}"
