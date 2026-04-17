#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$BASE_DIR/.venv/bin/python"
REQUIRED_MODULES=("django")

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
  echo "Run ./env-refresh.sh --deps-only" >&2
  exit 1
fi

if ! "$PYTHON_BIN" - "${REQUIRED_MODULES[@]}" <<'PY'
from __future__ import annotations

import importlib.util
import sys

required_modules = tuple(sys.argv[1:])
missing = [name for name in required_modules if importlib.util.find_spec(name) is None]
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
