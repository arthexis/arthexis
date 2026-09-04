#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

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

env_flag_enabled() {
  case "${1:-}" in
    1|true|TRUE|yes|YES)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

qa_cache_key_preferred() {
  env_flag_enabled "${ARTHEXIS_INCLUDE_QA_REQUIREMENTS:-}" || env_flag_enabled "${ARTHEXIS_INSTALL_PREVIEW_DEPS:-}"
}

hardware_cache_key_active() {
  local lock_dir="$BASE_DIR/.locks"
  local lcd_lock="${ARTHEXIS_LCD_LOCK:-lcd_screen.lck}"
  local rfid_service_lock="${ARTHEXIS_RFID_SERVICE_LOCK:-rfid-service.lck}"
  local rfid_lock="${ARTHEXIS_RFID_LOCK:-rfid.lck}"
  if env_flag_enabled "${ARTHEXIS_INSTALL_HARDWARE_DEPS:-}" || env_flag_enabled "${ARTHEXIS_INCLUDE_HARDWARE_REQUIREMENTS:-}"; then
    return 0
  fi
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

resolve_cached_venv() {
  local include_ci="$1"
  local -a venv_resolve_args=("$BASE_DIR")
  if [[ "$include_ci" == "1" ]]; then
    venv_resolve_args+=(--include-ci)
  fi
  if hardware_cache_key_active; then
    venv_resolve_args+=(--include-hardware)
  fi

  "$bootstrap_python" "$BASE_DIR/scripts/helpers/venv_path.py" "${venv_resolve_args[@]}"
}

candidate_venvs=()
if [[ -n "${ARTHEXIS_VENV_DIR:-}" ]]; then
  candidate_venvs+=("$ARTHEXIS_VENV_DIR")
elif [[ -n "${ARTHEXIS_ENV_ROOT:-}" ]]; then
  if bootstrap_python="$(find_bootstrap_python)"; then
    if qa_cache_key_preferred; then
      candidate_venvs+=("$(resolve_cached_venv 1)")
      candidate_venvs+=("$(resolve_cached_venv 0)")
    else
      candidate_venvs+=("$(resolve_cached_venv 0)")
      candidate_venvs+=("$(resolve_cached_venv 1)")
    fi
  fi
fi
candidate_venvs+=("$BASE_DIR/.venv" "$BASE_DIR/venv")

for venv_dir in "${candidate_venvs[@]}"; do
  for python_path in "$venv_dir/bin/python" "$venv_dir/Scripts/python.exe"; do
    if [[ -x "$python_path" ]]; then
      exec "$python_path" "$@"
    fi
  done
done

cat >&2 <<'MSG'
No project virtual environment Python was found.

Expected one of:
  $ARTHEXIS_VENV_DIR/bin/python (when ARTHEXIS_VENV_DIR is set)
  $ARTHEXIS_ENV_ROOT/venvs/<dependency-cache-key>/bin/python (when ARTHEXIS_ENV_ROOT is set)
  .venv/bin/python
  .venv/Scripts/python.exe
  venv/bin/python
  venv/Scripts/python.exe

Bootstrap the environment first:
  ./scripts/dev/dev-env.sh
  ./install.sh

Then rerun your command, for example:
  ./py manage.py test run -- apps/sites
MSG
exit 1
