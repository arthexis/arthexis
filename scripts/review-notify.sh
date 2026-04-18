#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$BASE_DIR"

PYTHON_BIN="$BASE_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || true)"
  if [ -n "$PYTHON_BIN" ] && ! "$PYTHON_BIN" -c "import sys; import django; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1; then
    PYTHON_BIN=""
  fi
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "Python runtime not found. Install Python 3 or run ./install.sh first." >&2
  exit 1
fi

exec "$PYTHON_BIN" manage.py review_notify "$@"
