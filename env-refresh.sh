#!/usr/bin/env bash

# Enable strict error handling with consistent POSIX newlines to avoid

# malformed `set` invocations when the script is copied between filesystems.
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIP_INSTALL_HELPER="$SCRIPT_DIR/scripts/helpers/pip_install.py"
PIP_CACHE_DIR="$SCRIPT_DIR/.cache/pip"
# Normalize helper scripts that might have been checked out with Windows line endings
sanitize_helper_newlines() {
  local target="$1"
  if [ ! -f "$target" ]; then
    return 0
  fi

  if LC_ALL=C grep -q $'\r' "$target"; then
    if command -v perl >/dev/null 2>&1; then
      perl -pi -e 's/\r$//' "$target"
    else
      local tmp
      tmp="$(mktemp)"
      tr -d '\r' <"$target" >"$tmp" && cat "$tmp" >"$target"
      rm -f "$tmp"
    fi
  fi
}
# shellcheck source=scripts/helpers/common.sh
sanitize_helper_newlines "$SCRIPT_DIR/scripts/helpers/common.sh"
. "$SCRIPT_DIR/scripts/helpers/common.sh"
# shellcheck source=scripts/helpers/logging.sh
sanitize_helper_newlines "$SCRIPT_DIR/scripts/helpers/logging.sh"
. "$SCRIPT_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/systemd_locks.sh
sanitize_helper_newlines "$SCRIPT_DIR/scripts/helpers/systemd_locks.sh"
. "$SCRIPT_DIR/scripts/helpers/systemd_locks.sh"
# shellcheck source=scripts/helpers/service_manager.sh
sanitize_helper_newlines "$SCRIPT_DIR/scripts/helpers/service_manager.sh"
if [ -f "$SCRIPT_DIR/scripts/helpers/service_manager.sh" ]; then
  . "$SCRIPT_DIR/scripts/helpers/service_manager.sh"
else
  echo "Warning: service_manager.sh not found; using default lock filenames." >&2
fi

ARTHEXIS_LCD_LOCK="${ARTHEXIS_LCD_LOCK:-lcd_screen.lck}"
ARTHEXIS_RFID_SERVICE_LOCK="${ARTHEXIS_RFID_SERVICE_LOCK:-rfid-service.lck}"

should_use_hardware_cache_key() {
  local lock_dir="$SCRIPT_DIR/.locks"
  local role_file="$lock_dir/role.lck"
  local lcd_lock="$ARTHEXIS_LCD_LOCK"
  local rfid_service_lock="$ARTHEXIS_RFID_SERVICE_LOCK"
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

  if [[ -f "$role_file" ]] && [[ "$(tr -d '\r\n' < "$role_file")" == "Control" ]]; then
    return 0
  fi

  if [[ -f "$lock_dir/$lcd_lock" || -f "$lock_dir/$rfid_service_lock" || -f "$lock_dir/$rfid_lock" ]]; then
    return 0
  fi

  return 1
}

now_ms() {
  date +%s%3N
}

elapsed_ms() {
  local start="$1"
  local now
  now=$(now_ms)
  echo $((now - start))
}

if [ -z "${ARTHEXIS_RUN_AS_USER:-}" ]; then
  TARGET_USER="$(arthexis_detect_service_user "$SCRIPT_DIR")"
  if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "root" ] && [ "$(id -un)" != "$TARGET_USER" ] && command -v sudo >/dev/null 2>&1 && sudo -n -u "$TARGET_USER" true >/dev/null 2>&1; then
    exec sudo -u "$TARGET_USER" ARTHEXIS_RUN_AS_USER="$TARGET_USER" "$SCRIPT_DIR/$(basename "$0")" "$@"
  fi
fi
if [ -z "$SCRIPT_DIR" ] || [ "$SCRIPT_DIR" = "/" ]; then
  echo "Refusing to run from root directory." >&2
  exit 1
fi
cd "$SCRIPT_DIR"
arthexis_resolve_log_dir "$SCRIPT_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
SCRIPT_START_MS=$(now_ms)

show_pip_failure() {
  local status=$1
  echo "pip failed with exit code ${status}. See the recent installer output below:" >&2
  if [ -f "$LOG_FILE" ]; then
    tail -n 40 "$LOG_FILE" >&2 || true
  fi
}

VENV_DIR=""
PYTHON=""
USE_SYSTEM_PYTHON=0
FORCE_REQUIREMENTS_INSTALL=0
LOCK_DIR="$SCRIPT_DIR/.locks"
ENV_REFRESH_PID_FILE="$LOCK_DIR/env-refresh.pid"
ENV_REFRESH_PID_DIR="$LOCK_DIR/env-refresh.pids"
FORCE_REFRESH=0
PIP_FRESHNESS_MINUTES=0
DEPS_ONLY=0
INSTALL_AND_REFRESH=0
MIGRATE_RECONCILE=0
AUTO_RECONCILE_ON_MISMATCH=0
WRITE_MIGRATIONS=0

LATEST=0
CLEAN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --latest)
      LATEST=1
      shift
      ;;
    --clean)
      CLEAN=1
      shift
      ;;
    --force-refresh)
      FORCE_REFRESH=1
      shift
      ;;
    --pip-freshness-minutes)
      PIP_FRESHNESS_MINUTES="$2"
      shift 2
      ;;
    --deps-only)
      DEPS_ONLY=1
      shift
      ;;
    --install-and-refresh)
      INSTALL_AND_REFRESH=1
      shift
      ;;
    --migrate)
      MIGRATE_RECONCILE=1
      shift
      ;;
    --reconcile)
      AUTO_RECONCILE_ON_MISMATCH=1
      shift
      ;;
    --write-migrations)
      WRITE_MIGRATIONS=1
      shift
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -gt 0 ]]; then
  echo "Unsupported env-refresh.sh option: $1" >&2
  exit 2
fi

if [ "$DEPS_ONLY" -eq 1 ] && [ "$INSTALL_AND_REFRESH" -eq 1 ]; then
  echo "Cannot combine --deps-only with --install-and-refresh." >&2
  exit 1
fi

INCLUDE_QA_REQUIREMENTS=0
if [[ "$DEPS_ONLY" -eq 1 ]]; then
  INCLUDE_QA_REQUIREMENTS=1
fi
INCLUDE_HARDWARE_REQUIREMENTS=0
if should_use_hardware_cache_key; then
  INCLUDE_HARDWARE_REQUIREMENTS=1
fi
export ARTHEXIS_INCLUDE_QA_REQUIREMENTS="$INCLUDE_QA_REQUIREMENTS"
export ARTHEXIS_INCLUDE_HARDWARE_REQUIREMENTS="$INCLUDE_HARDWARE_REQUIREMENTS"

env_refresh_process_start_time() {
  local pid="$1"
  if [ -r "/proc/$pid/stat" ]; then
    awk 'sub(/^.*\)/, "") {print $20}' "/proc/$pid/stat" 2>/dev/null || true
  fi
}

write_env_refresh_pid_file() {
  local physical_script_dir
  local pid_file
  local start_time
  local tmp_file
  local tmp_pointer_file
  physical_script_dir="$(cd "$SCRIPT_DIR" && pwd -P)"
  start_time="$(env_refresh_process_start_time "$$")"
  (umask 077 && mkdir -p "$ENV_REFRESH_PID_DIR")
  pid_file="$ENV_REFRESH_PID_DIR/$$.pid"
  tmp_file="${pid_file}.$$"
  tmp_pointer_file="${ENV_REFRESH_PID_FILE}.$$"
  (
    umask 077
    {
      printf 'pid=%s\n' "$$"
      printf 'start_time=%s\n' "$start_time"
      printf 'base_dir=%s\n' "$SCRIPT_DIR"
      printf 'physical_base_dir=%s\n' "$physical_script_dir"
    } >"$tmp_file"
  )
  mv "$tmp_file" "$pid_file"
  (
    umask 077
    {
      printf 'pid=%s\n' "$$"
      printf 'start_time=%s\n' "$start_time"
      printf 'base_dir=%s\n' "$SCRIPT_DIR"
      printf 'physical_base_dir=%s\n' "$physical_script_dir"
    } >"$tmp_pointer_file"
  )
  mv "$tmp_pointer_file" "$ENV_REFRESH_PID_FILE"
}

cleanup_env_refresh_pid_file() {
  local recorded_pid=""
  rm -f "$ENV_REFRESH_PID_DIR/$$.pid"
  rmdir "$ENV_REFRESH_PID_DIR" 2>/dev/null || true
  if [ -f "$ENV_REFRESH_PID_FILE" ]; then
    recorded_pid="$(awk -F= '$1 == "pid" {print $2; exit}' "$ENV_REFRESH_PID_FILE" 2>/dev/null || true)"
  fi
  if [ "$recorded_pid" = "$$" ]; then
    rm -f "$ENV_REFRESH_PID_FILE"
  fi
}

if [ "${ARTHEXIS_ENV_REFRESH_SOURCE_ONLY:-0}" != "1" ]; then
  mkdir -p "$LOCK_DIR"
  mkdir -p "$PIP_CACHE_DIR"
  write_env_refresh_pid_file
  trap cleanup_env_refresh_pid_file EXIT
fi

if ! bootstrap_python="$(arthexis_python_bin 2>/dev/null)"; then
  echo "Python interpreter not found. Run ./install.sh first. Skipping." >&2
  exit 0
fi
VENV_RESOLVE_ARGS=("$SCRIPT_DIR")
if [ "$INCLUDE_QA_REQUIREMENTS" -eq 1 ]; then
  VENV_RESOLVE_ARGS+=(--include-ci)
fi
if [ "$INCLUDE_HARDWARE_REQUIREMENTS" -eq 1 ]; then
  VENV_RESOLVE_ARGS+=(--include-hardware)
fi
VENV_DIR="$($bootstrap_python "$SCRIPT_DIR/scripts/helpers/venv_path.py" "${VENV_RESOLVE_ARGS[@]}")"
PYTHON="$VENV_DIR/bin/python"

if [ ! -f "$PYTHON" ]; then
  mkdir -p "$(dirname "$VENV_DIR")"
  if "$bootstrap_python" -m venv "$VENV_DIR" >/dev/null 2>&1; then
    PYTHON="$VENV_DIR/bin/python"
    USE_SYSTEM_PYTHON=0
    FORCE_REQUIREMENTS_INSTALL=1
    echo "Virtual environment not found. Bootstrapping new virtual environment at $VENV_DIR." >&2
  else
    PYTHON="$bootstrap_python"
    USE_SYSTEM_PYTHON=1
    echo "Virtual environment not found and automatic creation failed. Using system Python." >&2
  fi
fi


# Ensure pip is available; attempt to install if missing
if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
  echo "pip not found in virtual environment. Attempting to install with ensurepip..." >&2
  if "$PYTHON" -m ensurepip --upgrade >/dev/null 2>&1 && \
     "$PYTHON" -m pip --version >/dev/null 2>&1; then
    :
  else
    echo "Failed to install pip automatically. On Debian/Ubuntu/WSL, ensure python3-venv is installed and rerun ./install.sh." >&2
    exit 1
  fi
fi

pip_install_with_helper() {
  if [ -f "$PIP_INSTALL_HELPER" ]; then
    "$PYTHON" "$PIP_INSTALL_HELPER" "$@"
  else
    "$PYTHON" -m pip install "$@"
  fi
}

pip_binary_policy_args_for_requirements_file() {
  local requirements_file_basename="$1"

  case "$requirements_file_basename" in
    requirements.txt|requirements-ci.txt)
      echo "--only-binary=:all:"
      ;;
    *)
      echo ""
      ;;
  esac
  return 0
}

celery_requirement() {
  local requirements_file="$SCRIPT_DIR/requirements.txt"
  if [ -f "$requirements_file" ]; then
    local line
    line=$(grep -E '^celery(\[[^]]+\])?([[:space:]]*[<=>!~].*)?$' "$requirements_file" | head -n 1 || true)
    if [ -n "$line" ]; then
      echo "$line"
      return 0
    fi
  fi
  echo "celery"
}

ensure_celery_installed() {
  if "$PYTHON" - <<'PY' >/dev/null 2>&1
import importlib
importlib.import_module("celery")
PY
  then
    return 0
  fi

  local -a celery_pip_args=(--cache-dir "$PIP_CACHE_DIR")
  if [ "$USE_SYSTEM_PYTHON" -eq 1 ]; then
    celery_pip_args+=(--user)
  fi
  local celery_req
  celery_req=$(celery_requirement)

  echo "Celery not found; attempting to install ${celery_req}." >&2
  if ! pip_install_with_helper "${celery_pip_args[@]}" "$celery_req"; then
    echo "Celery installation failed. Ensure pip and Python venv support are installed." >&2
    echo "  Ubuntu/Debian: sudo apt install python3-venv" >&2
    echo "  RHEL/Fedora:   sudo dnf install python3-pip" >&2
    return 1
  fi
}

should_install_hardware_requirements() {
  should_use_hardware_cache_key
}

collect_requirement_files() {
  local -n out_array="$1"
  local ci_file="$SCRIPT_DIR/requirements-ci.txt"
  local hardware_file="$SCRIPT_DIR/requirements-hw.txt"

  if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    out_array+=("$SCRIPT_DIR/requirements.txt")
  fi

  if [ -f "$ci_file" ] && [ "$INCLUDE_QA_REQUIREMENTS" -eq 1 ]; then
    out_array+=("$ci_file")
  fi

  if [ -f "$hardware_file" ] && should_install_hardware_requirements; then
    out_array+=("$hardware_file")
  fi
}

compute_file_checksum() {
  local file="$1"
  if [ ! -f "$file" ]; then
    echo ""
    return 0
  fi

  sha256sum "$file" | awk '{print $1}'
}

compute_requirements_checksum() {
  local -a files=("$@")

  if [ ${#files[@]} -eq 0 ]; then
    echo ""
    return 0
  fi

  (
    for file in "${files[@]}"; do
      printf '%s\n' "${file##*/}"
      cat "$file"
    done
  ) | sha256sum | awk '{print $1}'
}

install_watch_upgrade_helper() {
  local helper_path="$SCRIPT_DIR/scripts/helpers/watch-upgrade.sh"
  local target_path="/usr/local/bin/watch-upgrade"

  if [ ! -f "$helper_path" ]; then
    return 0
  fi

  if [ ! -x "$helper_path" ] && [ -w "$helper_path" ]; then
    chmod +x "$helper_path" 2>/dev/null || true
  fi

  local target_dir
  target_dir="$(dirname "$target_path")"

  local needs_sudo=0
  if [ ! -w "$target_dir" ] || { [ -f "$target_path" ] && [ ! -w "$target_path" ]; }; then
    if command -v sudo >/dev/null 2>&1; then
      if arthexis_prime_sudo_credentials; then
        needs_sudo=1
      else
        echo "Skipping watch-upgrade helper installation: insufficient permissions for $target_dir." >&2
        echo "Re-run env-refresh.sh with elevated privileges to install /usr/local/bin/watch-upgrade." >&2
        return 0
      fi
    else
      echo "Skipping watch-upgrade helper installation: insufficient permissions for $target_dir." >&2
      echo "Re-run env-refresh.sh with elevated privileges to install /usr/local/bin/watch-upgrade." >&2
      return 0
    fi
  fi

  local -a prefix=()
  if [ "$needs_sudo" -eq 1 ]; then
    prefix=(sudo -n)
  fi

  if ! "${prefix[@]}" mkdir -p "$target_dir"; then
    echo "Unable to create $target_dir; skipping watch-upgrade helper installation." >&2
    echo "Re-run env-refresh.sh with appropriate privileges to complete installation." >&2
    return 0
  fi

  if ! "${prefix[@]}" cp "$helper_path" "$target_path"; then
    echo "Failed to copy watch-upgrade helper to $target_path; skipping installation." >&2
    echo "Re-run env-refresh.sh with appropriate privileges to complete installation." >&2
    return 0
  fi

  if ! "${prefix[@]}" chmod +x "$target_path"; then
    echo "Unable to set executable permissions on $target_path; skipping installation." >&2
    echo "Re-run env-refresh.sh with appropriate privileges to complete installation." >&2
    return 0
  fi
}

if [ "${ARTHEXIS_ENV_REFRESH_SOURCE_ONLY:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

if [ "$CLEAN" -eq 1 ]; then
  find "$SCRIPT_DIR" -maxdepth 1 -name 'db*.sqlite3' -delete
fi

REQ_SCAN_START_MS=$(now_ms)
collect_requirement_files REQUIREMENT_FILES
MARKER_ROOT="$VENV_DIR"
if [[ "$USE_SYSTEM_PYTHON" -eq 1 ]]; then
  MARKER_ROOT="$SCRIPT_DIR/.cache/env-refresh/system-python"
fi
mkdir -p "$MARKER_ROOT"
REQ_HASH_FILE="$MARKER_ROOT/.arthexis-requirements.bundle.sha256"
REQ_HASH_MANIFEST="$MARKER_ROOT/.arthexis-requirements.hashes"
REQ_TIMESTAMP_FILE="$MARKER_ROOT/.arthexis-requirements.install-ts"
STORED_REQ_HASH=""
[ -f "$REQ_HASH_FILE" ] && STORED_REQ_HASH=$(cat "$REQ_HASH_FILE")
REQUIREMENTS_HASH=""
if [ ${#REQUIREMENT_FILES[@]} -gt 0 ]; then
  REQUIREMENTS_HASH=$(compute_requirements_checksum "${REQUIREMENT_FILES[@]}")
fi

declare -A PREVIOUS_REQ_HASHES=()
declare -A CURRENT_REQ_HASHES=()
if [ -f "$REQ_HASH_MANIFEST" ]; then
  while read -r req_file stored_hash; do
    [ -z "$req_file" ] && continue
    PREVIOUS_REQ_HASHES["$req_file"]="$stored_hash"
  done <"$REQ_HASH_MANIFEST"
fi

CHANGED_REQUIREMENTS=()
for req_file in "${REQUIREMENT_FILES[@]}"; do
  req_key="$(basename "$req_file")"
  current_hash=$(compute_file_checksum "$req_file")
  CURRENT_REQ_HASHES["$req_key"]="$current_hash"
  previous_hash="${PREVIOUS_REQ_HASHES[$req_key]:-}"
  if [ "$current_hash" != "$previous_hash" ]; then
    CHANGED_REQUIREMENTS+=("$req_file")
  fi
done

REMOVED_REQUIREMENTS=0
for stored_req in "${!PREVIOUS_REQ_HASHES[@]}"; do
  found=0
  for req_file in "${REQUIREMENT_FILES[@]}"; do
    if [ "$stored_req" = "$(basename "$req_file")" ]; then
      found=1
      break
    fi
  done
  if [ "$found" -eq 0 ]; then
    REMOVED_REQUIREMENTS=1
    break
  fi
done
echo "Timing: requirement hash scan took $(elapsed_ms "$REQ_SCAN_START_MS")ms"

NEED_INSTALL=$FORCE_REQUIREMENTS_INSTALL
if [ -n "$REQUIREMENTS_HASH" ] && [ "$REQUIREMENTS_HASH" != "$STORED_REQ_HASH" ]; then
  NEED_INSTALL=1
fi
if [ "$FORCE_REFRESH" -eq 1 ]; then
  NEED_INSTALL=1
fi
RECENT_INSTALL=0
if [ "$PIP_FRESHNESS_MINUTES" -gt 0 ] && [ -f "$REQ_TIMESTAMP_FILE" ]; then
  LAST_INSTALL_TS=$(stat -c %Y "$REQ_TIMESTAMP_FILE" 2>/dev/null || echo 0)
  NOW_TS=$(date +%s)
  if [ $((NOW_TS - LAST_INSTALL_TS)) -lt $((PIP_FRESHNESS_MINUTES * 60)) ]; then
    RECENT_INSTALL=1
  fi
fi
if [ "$USE_SYSTEM_PYTHON" -eq 1 ] && [ "$NEED_INSTALL" -eq 0 ]; then
  if ! "$PYTHON" - <<'PY' >/dev/null 2>&1
import importlib
import sys

try:
    importlib.import_module("django")
except ModuleNotFoundError:
    sys.exit(1)
PY
  then
    NEED_INSTALL=1
  fi
fi
if [ "$NEED_INSTALL" -eq 1 ] && [ "$RECENT_INSTALL" -eq 1 ] && [ "$FORCE_REFRESH" -eq 0 ]; then
  echo "requirements checksum changed recently—skipping pip (fresh within ${PIP_FRESHNESS_MINUTES}m)"
  NEED_INSTALL=0
fi

if [ ${#REQUIREMENT_FILES[@]} -eq 0 ]; then
  echo "No requirements*.txt files found; skipping dependency installation."
elif [ "$NEED_INSTALL" -eq 0 ]; then
  echo "dependencies unchanged—env refresh skipped"
else
  install_targets=()
  if [ "$FORCE_REFRESH" -eq 1 ] || [ "$FORCE_REQUIREMENTS_INSTALL" -eq 1 ] || [ "$REMOVED_REQUIREMENTS" -eq 1 ]; then
    install_targets=("${REQUIREMENT_FILES[@]}")
  elif [ ${#CHANGED_REQUIREMENTS[@]} -gt 0 ]; then
    install_targets=("${CHANGED_REQUIREMENTS[@]}")
  else
    install_targets=("${REQUIREMENT_FILES[@]}")
  fi

  if [ ${#CHANGED_REQUIREMENTS[@]} -gt 0 ] && [ "$FORCE_REFRESH" -eq 0 ] && [ "$FORCE_REQUIREMENTS_INSTALL" -eq 0 ]; then
    echo "Detected updates in: ${CHANGED_REQUIREMENTS[*]}"
  elif [ "$REMOVED_REQUIREMENTS" -eq 1 ]; then
    echo "Detected removed requirement files; reinstalling remaining requirements"
  fi

  pip_args=(--cache-dir "$PIP_CACHE_DIR")
  if [ "$USE_SYSTEM_PYTHON" -eq 1 ]; then
    pip_args+=(--user)
  fi
  PIP_SECTION_START_MS=$(now_ms)
  for req_file in "${install_targets[@]}"; do
    FILE_INSTALL_START_MS=$(now_ms)
    req_policy_args=$(pip_binary_policy_args_for_requirements_file "$(basename "$req_file")")
    if [[ -n "$req_policy_args" ]]; then
      # shellcheck disable=SC2206
      pip_policy_args=( $req_policy_args )
    else
      pip_policy_args=()
    fi
    if pip_install_with_helper "${pip_args[@]}" "${pip_policy_args[@]}" -r "$req_file"; then
      :
    else
      pip_status=$?
      show_pip_failure "$pip_status"
      exit "$pip_status"
    fi
    echo "Timing: pip install ${req_file##*/} took $(elapsed_ms "$FILE_INSTALL_START_MS")ms"
  done
  echo "Timing: pip installation block took $(elapsed_ms "$PIP_SECTION_START_MS")ms"
  if [ -n "$REQUIREMENTS_HASH" ]; then
    echo "$REQUIREMENTS_HASH" > "$REQ_HASH_FILE"
  fi
  if [ ${#CURRENT_REQ_HASHES[@]} -gt 0 ]; then
    : >"$REQ_HASH_MANIFEST"
    for req_file in "${REQUIREMENT_FILES[@]}"; do
      req_key="$(basename "$req_file")"
      printf '%s %s\n' "$req_key" "${CURRENT_REQ_HASHES[$req_key]}" >>"$REQ_HASH_MANIFEST"
    done
  fi
  date +%s > "$REQ_TIMESTAMP_FILE"
fi

ensure_editable_checkout_installed() {
  if [ "$USE_SYSTEM_PYTHON" -eq 1 ]; then
    return 0
  fi

  pip_install_with_helper --cache-dir "$PIP_CACHE_DIR" --no-build-isolation --no-deps -e "$SCRIPT_DIR"
}

ensure_editable_checkout_installed
ensure_celery_installed

if [ "$DEPS_ONLY" -eq 1 ]; then
  echo "Dependency refresh complete; skipping env-refresh database updates."
  exit 0
fi

if [ "$INSTALL_AND_REFRESH" -eq 1 ]; then
  echo "Dependency refresh complete; continuing with env-refresh in the same transaction."
fi

install_watch_upgrade_helper || echo "watch-upgrade helper setup failed unexpectedly; delegated auto-upgrades may be unavailable"

# Ensure systemd units run as the project owner, matching the install user.
arthexis_update_systemd_service_user "$SCRIPT_DIR" "$SCRIPT_DIR/.locks" || true

ARGS=""
if [ "$LATEST" -eq 1 ]; then
  ARGS="$ARGS --latest"
fi
if [ "$CLEAN" -eq 1 ]; then
  ARGS="$ARGS --clean"
fi
if [ "$MIGRATE_RECONCILE" -eq 1 ]; then
  ARGS="$ARGS --migrate"
fi
if [[ "$AUTO_RECONCILE_ON_MISMATCH" -eq 1 ]]; then
  ARGS="$ARGS --reconcile"
fi
if [[ "$WRITE_MIGRATIONS" -eq 1 ]]; then
  ARGS="$ARGS --write-migrations"
fi
"$PYTHON" env-refresh.py $ARGS database
echo "Timing: env-refresh.sh completed in $(elapsed_ms "$SCRIPT_START_MS")ms"
