#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$BASE_DIR/.venv/bin/python"
PYTHON_LABEL=".venv/bin/python"
if [[ "${OSTYPE:-}" == "msys" || "${OSTYPE:-}" == "cygwin" || "${OSTYPE:-}" == "win32" ]]; then
  PYTHON_BIN="$BASE_DIR/.venv/Scripts/python.exe"
  PYTHON_LABEL=".venv/Scripts/python.exe"
fi
REQUIRED_MODULES=("django")
ENV_REFRESH_CMD=("$BASE_DIR/env-refresh.sh" "--deps-only")

python_is_usable() {
  [[ -x "$PYTHON_BIN" ]] && "$PYTHON_BIN" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1
}

ensure_venv_python() {
  if python_is_usable; then
    return 0
  fi

  echo "$PYTHON_LABEL missing or unusable at $PYTHON_BIN; running ${ENV_REFRESH_CMD[*]}" >&2
  "${ENV_REFRESH_CMD[@]}"

  if python_is_usable; then
    return 0
  fi

  echo "$PYTHON_LABEL missing: expected executable at $PYTHON_BIN after ${ENV_REFRESH_CMD[*]}" >&2
  echo "Run ./env-refresh.sh --deps-only" >&2
  return 1
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

if ! ensure_venv_python; then
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
