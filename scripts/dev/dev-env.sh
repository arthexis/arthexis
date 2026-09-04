#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEFAULT_ENV_ROOT="${HOME:-$BASE_DIR}/.cache/arthexis"
MODE="auto"

usage() {
  cat <<'MSG'
Usage: ./scripts/dev/dev-env.sh [--auto|--local|--install]

Fast contributor bootstrap for Arthexis.

Modes:
  --auto       Use the native local virtualenv/install path.
  --local      Reuse a dependency-warmed virtualenv from ARTHEXIS_VENV_DIR or
               ARTHEXIS_ENV_ROOT, falling back to ./install.sh when no cache exists.
  --install    Run the canonical ./install.sh bootstrap path.

Environment:
  ARTHEXIS_ENV_ROOT    Shared env root; uses $ARTHEXIS_ENV_ROOT/venv.
  ARTHEXIS_VENV_DIR    Exact shared virtualenv directory; overrides ARTHEXIS_ENV_ROOT.

Examples:
  ./scripts/dev/dev-env.sh
  ARTHEXIS_ENV_ROOT="$HOME/.cache/arthexis" ./scripts/dev/dev-env.sh --local
  ARTHEXIS_VENV_DIR="$HOME/.cache/arthexis/venv" ./py manage.py check
MSG
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --auto)
      MODE="auto"
      shift
      ;;
    --local)
      MODE="local"
      shift
      ;;
    --install)
      MODE="install"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

shared_venv_dir() {
  if [[ -n "${ARTHEXIS_VENV_DIR:-}" ]]; then
    printf '%s\n' "$ARTHEXIS_VENV_DIR"
  else
    printf '%s\n' "${ARTHEXIS_ENV_ROOT:-$DEFAULT_ENV_ROOT}/venv"
  fi
}

venv_python() {
  local venv_dir="$1"
  if [[ -x "$venv_dir/bin/python" ]]; then
    printf '%s\n' "$venv_dir/bin/python"
  elif [[ -x "$venv_dir/Scripts/python.exe" ]]; then
    printf '%s\n' "$venv_dir/Scripts/python.exe"
  fi
}

run_local_path() {
  cd "$BASE_DIR"
  local cache_dir python_path
  cache_dir="$(shared_venv_dir)"
  python_path="$(venv_python "$cache_dir" || true)"

  if [[ -n "$python_path" ]] && "$python_path" -m pip --version >/dev/null 2>&1; then
    echo "Using shared virtualenv cache: $cache_dir"
    cat <<MSG
Shared virtualenv is ready. Use it with:
  ARTHEXIS_VENV_DIR="$cache_dir" ./py manage.py check
  ARTHEXIS_VENV_DIR="$cache_dir" ./py manage.py test run -- <target>
MSG
    return 0
  fi

  python_path="$(venv_python "$BASE_DIR/.venv" || true)"
  if [[ -n "$python_path" ]] && "$python_path" -m pip --version >/dev/null 2>&1; then
    echo "Using existing project virtualenv: $BASE_DIR/.venv"
    return 0
  fi

  echo "No dependency-warmed shared virtualenv was found at: $cache_dir"
  echo "Falling back to canonical ./install.sh bootstrap."
  exec "$BASE_DIR/install.sh"
}

case "$MODE" in
  auto)
    run_local_path
    ;;
  local)
    run_local_path
    ;;
  install)
    exec "$BASE_DIR/install.sh"
    ;;
  *)
    echo "Unsupported mode: $MODE" >&2
    exit 2
    ;;
esac
