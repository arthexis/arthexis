#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN=$(command -v python3 || true)
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "Python interpreter not found. Run ./install.sh --terminal --no-start first." >&2
  exit 1
fi

echo "Bootstrapping screenshot prerequisites with env-refresh..."
if ! "$ROOT_DIR/env-refresh.sh" --clean; then
  echo "Environment refresh failed. Consider rerunning ./install.sh --terminal --no-start." >&2
  exit 1
fi

# Prefer the freshly created virtualenv if it was added during env-refresh.
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

if ! "$PYTHON_BIN" - <<'PY'
import sys
try:
    import django  # noqa: F401
except ModuleNotFoundError:
    sys.exit(1)
PY
then
  echo "Django is unavailable. Run ./install.sh --terminal --no-start to install dependencies." >&2
  exit 1
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" manage.py prepare_screenshot_feature --role Terminal
