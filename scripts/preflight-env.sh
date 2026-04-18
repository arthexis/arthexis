#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$BASE_DIR/.venv/bin/python"
REQUIRED_MODULES=("django")

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
  echo "Bootstrapping dependencies with ./env-refresh.sh --deps-only" >&2
  if ! "$BASE_DIR/env-refresh.sh" --deps-only; then
    echo "Failed to bootstrap environment with ./env-refresh.sh --deps-only" >&2
    exit 1
  fi
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo ".venv/bin/python still missing after bootstrap: expected executable at $PYTHON_BIN" >&2
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
