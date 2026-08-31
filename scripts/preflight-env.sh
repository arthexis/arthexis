#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
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
  - the resolved Arthexis virtual environment Python exists and is executable
  - required Python tooling is importable for Arthexis entrypoints

Options:
  --pytest  additionally require pytest to be importable
USAGE
  exit 0
elif [[ $# -gt 0 ]]; then
  echo "Unknown option: $1" >&2
  exit 1
fi

find_bootstrap_python() {
  local candidate
  for candidate in "${ARTHEXIS_PYTHON_BIN:-}" python3 python; do
    if [[ -n "$candidate" ]] && command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

hardware_cache_key_active() {
  local lock_dir="$BASE_DIR/.locks"
  local lcd_lock="${ARTHEXIS_LCD_LOCK:-lcd_screen.lck}"
  local rfid_service_lock="${ARTHEXIS_RFID_SERVICE_LOCK:-rfid-service.lck}"
  local rfid_lock="${ARTHEXIS_RFID_LOCK:-rfid.lck}"

  case "${ARTHEXIS_INSTALL_HARDWARE_DEPS:-}" in
    1|true|TRUE|yes|YES)
      return 0
      ;;
    *)
      ;;
  esac

  if [[ -f "$lock_dir/control.lck" ]]; then
    return 0
  fi
  if [[ -f "$lock_dir/role.lck" ]] && [[ "$(tr -d '\r\n' < "$lock_dir/role.lck")" == "Control" ]]; then
    return 0
  fi
  if [[ -f "$lock_dir/$lcd_lock" || -f "$lock_dir/$rfid_service_lock" || -f "$lock_dir/$rfid_lock" ]]; then
    return 0
  fi
  return 1
}

candidate_dirs=()
venv_resolve_args=("$BASE_DIR")
if [[ "${1:-}" == "--pytest" ]]; then
  venv_resolve_args+=(--include-ci)
fi
if hardware_cache_key_active; then
  venv_resolve_args+=(--include-hardware)
fi
if [[ -n "${ARTHEXIS_VENV_DIR:-}" ]]; then
  candidate_dirs+=("$ARTHEXIS_VENV_DIR")
elif [[ -n "${ARTHEXIS_ENV_ROOT:-}" ]]; then
  if bootstrap_python="$(find_bootstrap_python)"; then
    candidate_dirs+=("$($bootstrap_python "$BASE_DIR/scripts/helpers/venv_path.py" "${venv_resolve_args[@]}")")
  fi
fi
candidate_dirs+=("$BASE_DIR/.venv" "$BASE_DIR/venv")

PYTHON_BIN=""
for venv_dir in "${candidate_dirs[@]}"; do
  for python_path in "$venv_dir/bin/python" "$venv_dir/Scripts/python.exe"; do
    if [[ -x "$python_path" ]]; then
      PYTHON_BIN="$python_path"
      break 2
    fi
  done
done

if [[ -z "$PYTHON_BIN" ]]; then
  echo "Resolved virtual environment Python missing." >&2
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
