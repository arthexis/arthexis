#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$BASE_DIR/.venv/bin/python"
ENV_REFRESH_CMD=("$BASE_DIR/env-refresh.sh" "--deps-only")
CI_REQUIREMENTS_FILE="$BASE_DIR/requirements-ci.txt"
REQUIRED_MODULES=("django")

check_required_modules() {
  "$PYTHON_BIN" - "${REQUIRED_MODULES[@]}" <<'PY'
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
}

try_env_refresh() {
  if [[ ! -x "${ENV_REFRESH_CMD[0]}" ]]; then
    echo "Cannot auto-refresh dependencies: missing ${ENV_REFRESH_CMD[0]}" >&2
    return 1
  fi

  echo "Attempting dependency bootstrap via: ${ENV_REFRESH_CMD[*]}" >&2
  "${ENV_REFRESH_CMD[@]}"
}

try_install_ci_requirements() {
  if [[ ! -f "$CI_REQUIREMENTS_FILE" ]]; then
    echo "Cannot auto-install CI requirements: missing $CI_REQUIREMENTS_FILE" >&2
    return 1
  fi
  if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Cannot auto-install CI requirements: missing python executable at $PYTHON_BIN" >&2
    return 1
  fi

  echo "Attempting CI dependency bootstrap via: $PYTHON_BIN -m pip install --only-binary=:all: -r $CI_REQUIREMENTS_FILE" >&2
  "$PYTHON_BIN" -m pip install --only-binary=:all: -r "$CI_REQUIREMENTS_FILE"
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

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo ".venv/bin/python missing: expected executable at $PYTHON_BIN" >&2
  if ! try_env_refresh || [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Run ./env-refresh.sh --deps-only" >&2
    exit 1
  fi
fi

if ! check_required_modules
then
  if ! try_env_refresh; then
    echo "Run ./env-refresh.sh --deps-only" >&2
    exit 1
  fi
  if ! check_required_modules; then
    if [[ " ${REQUIRED_MODULES[*]} " == *" pytest "* ]] && try_install_ci_requirements && check_required_modules; then
      exit 0
    fi
    echo "Run ./env-refresh.sh --deps-only" >&2
    exit 1
  fi
fi
