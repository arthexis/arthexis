#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$BASE_DIR/.venv/bin/python"
REQUIRED_MODULES=("django")
ENV_REFRESH_CMD=("$BASE_DIR/env-refresh.sh" "--deps-only")

ensure_venv_python() {
  local needs_refresh=0

  if [[ ! -x "$PYTHON_BIN" ]]; then
    needs_refresh=1
  elif ! "$PYTHON_BIN" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1; then
    needs_refresh=1
  fi

  if [[ "$needs_refresh" -eq 0 ]]; then
    return 0
  fi

  echo ".venv/bin/python missing or unusable at $PYTHON_BIN; running ${ENV_REFRESH_CMD[*]}" >&2
  "${ENV_REFRESH_CMD[@]}"
}

if [[ $# -gt 1 ]]; then
  echo "Too many arguments provided." >&2
  exit 1
fi

if [[ "${1:-}" == "--pytest" ]]; then
  REQUIRED_MODULES+=("pytest")
elif [[ "${1:-}" == "--help" ]]; then
  cat <<'USAGE'
Usage: ./scripts/preflight-env.sh [--pytest]

Checks that:
  - .venv/bin/python exists and is executable
  - required Python tooling is importable for Arthexis entrypoints

Options:
  --pytest  additionally require pytest to be importable
USAGE
  exit 0
elif [[ $# -gt 0 ]]; then
  echo "Unknown option: $1" >&2
  exit 1
fi

ensure_venv_python

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo ".venv/bin/python missing: expected executable at $PYTHON_BIN after ${ENV_REFRESH_CMD[*]}" >&2
  echo "Run ./env-refresh.sh --deps-only" >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1; then
  echo ".venv/bin/python is not runnable at $PYTHON_BIN after ${ENV_REFRESH_CMD[*]}" >&2
  echo "Run ./env-refresh.sh --deps-only" >&2
  exit 1
fi

if ! "$PYTHON_BIN" - "${REQUIRED_MODULES[@]}" <<'PY'
from __future__ import annotations

import importlib
import sys

required_modules = tuple(sys.argv[1:])
missing = []
for name in required_modules:
    try:
        importlib.import_module(name)
    except ImportError:
        missing.append(name)
if missing:
    print(
        "Required Python tooling not importable: "
        + ", ".join(missing),
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
then
  echo "Run ./env-refresh.sh --deps-only" >&2
  exit 1
fi
