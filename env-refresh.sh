#!/usr/bin/env bash

# Enable strict error handling with consistent POSIX newlines to avoid

# malformed `set` invocations when the script is copied between filesystems.
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIP_INSTALL_HELPER="$SCRIPT_DIR/scripts/helpers/pip_install.py"
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
# shellcheck source=scripts/helpers/logging.sh
sanitize_helper_newlines "$SCRIPT_DIR/scripts/helpers/logging.sh"
. "$SCRIPT_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/systemd_locks.sh
sanitize_helper_newlines "$SCRIPT_DIR/scripts/helpers/systemd_locks.sh"
. "$SCRIPT_DIR/scripts/helpers/systemd_locks.sh"

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

show_pip_failure() {
  local status=$1
  echo "pip failed with exit code ${status}. See the recent installer output below:" >&2
  if [ -f "$LOG_FILE" ]; then
    tail -n 40 "$LOG_FILE" >&2 || true
  fi
}

VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"
USE_SYSTEM_PYTHON=0
FORCE_REQUIREMENTS_INSTALL=0
LOCK_DIR="$SCRIPT_DIR/.locks"

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
    *)
      break
      ;;
  esac
done

if [ ! -f "$PYTHON" ]; then
  if command -v python3 >/dev/null 2>&1; then
    if python3 -m venv "$VENV_DIR" >/dev/null 2>&1; then
      PYTHON="$VENV_DIR/bin/python"
      USE_SYSTEM_PYTHON=0
      FORCE_REQUIREMENTS_INSTALL=1
      echo "Virtual environment not found. Bootstrapping new virtual environment." >&2
    else
      PYTHON="$(command -v python3)"
      USE_SYSTEM_PYTHON=1
      echo "Virtual environment not found and automatic creation failed. Using system Python." >&2
    fi
  else
    echo "Python interpreter not found. Run ./install.sh first. Skipping." >&2
    exit 0
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
    if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
      needs_sudo=1
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


mkdir -p "$LOCK_DIR"

if [ "$CLEAN" -eq 1 ]; then
  find "$SCRIPT_DIR" -maxdepth 1 -name 'db*.sqlite3' -delete
fi

REQ_FILE="$SCRIPT_DIR/requirements.txt"
if [ -f "$REQ_FILE" ]; then
  MD5_FILE="$LOCK_DIR/requirements.md5"
  NEW_HASH=$(md5sum "$REQ_FILE" | awk '{print $1}')
  STORED_HASH=""
  [ -f "$MD5_FILE" ] && STORED_HASH=$(cat "$MD5_FILE")
  NEED_INSTALL=0
  if [ "$NEW_HASH" != "$STORED_HASH" ]; then
    NEED_INSTALL=1
  elif [ "$USE_SYSTEM_PYTHON" -eq 1 ]; then
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
  if [ "$NEED_INSTALL" -eq 1 ]; then
    pip_args=()
    if [ "$USE_SYSTEM_PYTHON" -eq 1 ]; then
      pip_args+=(--user)
    fi
    if pip_install_with_helper "${pip_args[@]}" -r "$REQ_FILE"; then
      echo "$NEW_HASH" > "$MD5_FILE"
    else
      pip_status=$?
      show_pip_failure "$pip_status"
      exit "$pip_status"
    fi
  fi
elif [ -f "$REQ_FILE" ]; then
  MD5_FILE="$LOCK_DIR/requirements.system.md5"
  NEW_HASH=$(md5sum "$REQ_FILE" | awk '{print $1}')
  STORED_HASH=""
  [ -f "$MD5_FILE" ] && STORED_HASH=$(cat "$MD5_FILE")
  if [ "$NEW_HASH" != "$STORED_HASH" ]; then
    if pip_install_with_helper -r "$REQ_FILE"; then
      echo "$NEW_HASH" > "$MD5_FILE"
    else
      pip_status=$?
      show_pip_failure "$pip_status"
      echo "Failed to install project requirements with system Python. Run ./install.sh." >&2
      exit "$pip_status"
    fi
  fi
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
"$PYTHON" env-refresh.py $ARGS database
