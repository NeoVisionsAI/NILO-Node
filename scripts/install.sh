#!/usr/bin/env bash
# First-time install wrapper — see scripts/deploy.sh for all commands.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${DIR}/deploy.sh" install "$@"
