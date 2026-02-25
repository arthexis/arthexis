#!/usr/bin/env bash
set -euo pipefail

if command -v arthexis >/dev/null 2>&1; then
  exec arthexis resolve "$@"
fi

exec "${PYTHON:-python}" -m arthexis resolve "$@"
