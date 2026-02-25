#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

if command -v arthexis >/dev/null 2>&1; then
  exec arthexis resolve "$@"
fi

export PYTHONPATH="$BASE_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "${PYTHON:-python}" -m arthexis resolve "$@"
