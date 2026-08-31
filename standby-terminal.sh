#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$BASE_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3 || command -v python)"
fi

exec "$PYTHON" "$BASE_DIR/scripts/standby_terminal.py" "$@"
