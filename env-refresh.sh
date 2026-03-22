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

VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"
USE_SYSTEM_PYTHON=0
FORCE_REQUIREMENTS_INSTALL=0
LOCK_DIR="$SCRIPT_DIR/.locks"
FORCE_REFRESH=0
PIP_FRESHNESS_MINUTES=0
DEPS_ONLY=0
INSTALL_AND_REFRESH=0
INSTALL_PREVIEW_DEPS=0

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
    --preview-deps)
      INSTALL_PREVIEW_DEPS=1
      shift
      ;;
    *)
      break
      ;;
  esac
done

if [ "$DEPS_ONLY" -eq 1 ] && [ "$INSTALL_AND_REFRESH" -eq 1 ]; then
  echo "Cannot combine --deps-only with --install-and-refresh." >&2
  exit 1
fi

if [ ! -f "$PYTHON" ]; then
  if bootstrap_python="$(arthexis_python_bin 2>/dev/null)"; then
    if "$bootstrap_python" -m venv "$VENV_DIR" >/dev/null 2>&1; then
      PYTHON="$VENV_DIR/bin/python"
      USE_SYSTEM_PYTHON=0
      FORCE_REQUIREMENTS_INSTALL=1
      echo "Virtual environment not found. Bootstrapping new virtual environment." >&2
    else
      PYTHON="$bootstrap_python"
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

celery_requirement() {
  local requirements_file="$SCRIPT_DIR/requirements-runtime.txt"
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

should_install_preview_dependencies() {
  case "${ARTHEXIS_INSTALL_PREVIEW_DEPS:-}" in
    1|true|TRUE|yes|YES)
      return 0
      ;;
  esac

  if [ "$INSTALL_PREVIEW_DEPS" -eq 1 ]; then
    return 0
  fi

  return 1
}

playwright_requirement() {
  local requirements_file="$SCRIPT_DIR/requirements-preview.txt"
  if [ -f "$requirements_file" ]; then
    local line
    line=$(grep -E '^playwright([[:space:]]*[<=>!~].*)?$' "$requirements_file" | head -n 1 || true)
    if [ -n "$line" ]; then
      echo "$line"
      return 0
    fi
  fi
  echo "playwright"
}

selenium_requirement() {
  # Resolve the selenium requirement from the preview profile when present.
  local requirements_file="$SCRIPT_DIR/requirements-preview.txt"
  if [[ -f "$requirements_file" ]]; then
    local line
    line=$(grep -E '^[[:space:]]*selenium(\[[^]]*\])?([[:space:]]*[<=>!~][^;]*)?([[:space:]]*;.*)?[[:space:]]*$' "$requirements_file" | head -n 1 || true)
    if [[ -n "$line" ]]; then
      echo "$line"
      return 0
    fi
  fi
  echo "selenium"
}

ensure_selenium_installed() {
  # Ensure selenium is importable, installing it from the pinned requirement when needed.
  if "$PYTHON" -c 'import importlib; importlib.import_module("selenium")' >/dev/null 2>&1; then
    return 0
  fi

  local -a selenium_pip_args=(--cache-dir "$PIP_CACHE_DIR")
  if [[ "$USE_SYSTEM_PYTHON" -eq 1 ]]; then
    selenium_pip_args+=(--user)
  fi

  local selenium_req
  selenium_req=$(selenium_requirement)
  echo "Selenium not found; attempting to install ${selenium_req}." >&2
  if ! pip_install_with_helper "${selenium_pip_args[@]}" "$selenium_req"; then
    echo "Selenium installation failed. Ensure pip and Python venv support are installed." >&2
    return 1
  fi
}

playwright_version() {
  "$PYTHON" - <<'PY'
import importlib.metadata

try:
    print(importlib.metadata.version("playwright"))
except importlib.metadata.PackageNotFoundError:
    raise SystemExit(1)
PY
}

ensure_playwright_installed() {
  if playwright_version >/dev/null 2>&1; then
    return 0
  fi

  local -a playwright_pip_args=(--cache-dir "$PIP_CACHE_DIR")
  if [ "$USE_SYSTEM_PYTHON" -eq 1 ]; then
    playwright_pip_args+=(--user)
  fi

  local playwright_req
  playwright_req=$(playwright_requirement)
  echo "Playwright not found; attempting to install ${playwright_req}." >&2
  if ! pip_install_with_helper "${playwright_pip_args[@]}" "$playwright_req"; then
    echo "Playwright installation failed. Ensure pip and Python venv support are installed." >&2
    return 1
  fi
}

playwright_missing_host_dependencies() {
  "$PYTHON" - <<'PY'
import sys

try:
    from playwright.sync_api import Error, sync_playwright
except Exception as exc:
    print(exc, file=sys.stderr)
    raise SystemExit(1)

try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        browser.close()
except Error as exc:
    message = str(exc)
    if "Host system is missing dependencies" in message:
        print(message, file=sys.stderr)
        raise SystemExit(10)
    print(message, file=sys.stderr)
    raise SystemExit(1)
PY
}

ensure_playwright_host_dependencies() {
  if [ "${ARTHEXIS_SKIP_PLAYWRIGHT_INSTALL_DEPS:-0}" = "1" ]; then
    echo "Skipping Playwright host dependency installation because ARTHEXIS_SKIP_PLAYWRIGHT_INSTALL_DEPS=1."
    return 0
  fi

  if [ "$(uname -s)" != "Linux" ]; then
    return 0
  fi

  local -a install_deps_cmd=("$PYTHON" -m playwright install-deps chromium firefox)
  if [ "$(id -u)" -eq 0 ]; then
    echo "Installing Playwright host libraries for Linux."
    if ! "${install_deps_cmd[@]}"; then
      echo "Warning: Host dependency installation failed, but continuing to verification." >&2
    fi
    return 0
  fi

  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    echo "Installing Playwright host libraries for Linux via sudo."
    if ! sudo -n "${install_deps_cmd[@]}"; then
      echo "Warning: Host dependency installation via sudo failed, but continuing to verification." >&2
    fi
    return 0
  fi

  echo "Warning: Playwright browser binaries are installed, but Linux host libraries may still be missing." >&2
  echo "Run '$PYTHON -m playwright install-deps chromium firefox' with root access if preview capture still fails." >&2
  return 0
}

ensure_playwright_browsers_installed() {
  local browser_marker_file="$LOCK_DIR/playwright.version"
  local current_version=""
  local stored_version=""
  local verify_status=0

  if ! ensure_playwright_installed; then
    return 1
  fi

  current_version="$(playwright_version)"

  if [ -f "$browser_marker_file" ]; then
    stored_version="$(cat "$browser_marker_file")"
  fi

  if [ "$FORCE_REFRESH" -ne 0 ] || [ "$current_version" != "$stored_version" ]; then
    echo "Installing Playwright browser runtimes (chromium, firefox) for version ${current_version}."
    if ! "$PYTHON" -m playwright install chromium firefox; then
      echo "Playwright browser runtime installation failed." >&2
      return 1
    fi
  else
    echo "playwright browsers already installed for version ${current_version}; skipping"
  fi

  if ! ensure_playwright_host_dependencies; then
    echo "Playwright host dependency installation failed." >&2
    return 1
  fi

  set +e
  playwright_missing_host_dependencies
  verify_status=$?
  set -e
  if [ "$verify_status" -ne 0 ]; then
    if [ "$verify_status" -eq 10 ]; then
      echo "Warning: Playwright browser runtimes are installed, but this Linux environment is still missing host libraries for browser execution." >&2
      echo "Run '$PYTHON -m playwright install-deps chromium firefox' or install the packages reported above." >&2
    else
      echo "Warning: Playwright browser verification failed after installation." >&2
    fi
    return 0
  fi

  if [ "$FORCE_REFRESH" -ne 0 ] || [ "$current_version" != "$stored_version" ]; then
    printf '%s\n' "$current_version" > "$browser_marker_file"
  fi
}

should_install_hardware_requirements() {
  local lock_dir="$SCRIPT_DIR/.locks"
  local role_file="$lock_dir/role.lck"
  local lcd_lock="$ARTHEXIS_LCD_LOCK"
  local rfid_service_lock="$ARTHEXIS_RFID_SERVICE_LOCK"
  local rfid_lock="rfid.lck"

  case "${ARTHEXIS_INSTALL_HARDWARE_DEPS:-}" in
    1|true|TRUE|yes|YES)
      return 0
      ;;
  esac

  if [ -f "$role_file" ] && [ "$(tr -d '\r\n' < "$role_file")" = "Control" ]; then
    return 0
  fi

  if [ -f "$lock_dir/$lcd_lock" ] || [ -f "$lock_dir/$rfid_service_lock" ] || [ -f "$lock_dir/$rfid_lock" ]; then
    return 0
  fi

  return 1
}

collect_requirement_files() {
  local -n out_array="$1"
  local runtime_file="$SCRIPT_DIR/requirements-runtime.txt"
  local hardware_file="$SCRIPT_DIR/requirements-hw.txt"
  local preview_file="$SCRIPT_DIR/requirements-preview.txt"

  if [ -f "$runtime_file" ]; then
    out_array+=("$runtime_file")
  fi

  if [ -f "$hardware_file" ] && should_install_hardware_requirements; then
    out_array+=("$hardware_file")
  fi

  if [ -f "$preview_file" ] && should_install_preview_dependencies; then
    out_array+=("$preview_file")
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


mkdir -p "$LOCK_DIR"
mkdir -p "$PIP_CACHE_DIR"

if [ "$CLEAN" -eq 1 ]; then
  find "$SCRIPT_DIR" -maxdepth 1 -name 'db*.sqlite3' -delete
fi

REQ_SCAN_START_MS=$(now_ms)
collect_requirement_files REQUIREMENT_FILES
REQ_HASH_FILE="$LOCK_DIR/requirements.bundle.sha256"
REQ_HASH_MANIFEST="$LOCK_DIR/requirements.hashes"
REQ_TIMESTAMP_FILE="$LOCK_DIR/requirements.install-ts"
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
    if pip_install_with_helper "${pip_args[@]}" -r "$req_file"; then
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

ensure_celery_installed
if should_install_preview_dependencies; then
  ensure_playwright_browsers_installed
  ensure_selenium_installed
else
  echo "Preview/browser dependencies not requested; skipping Playwright and Selenium setup."
fi

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
"$PYTHON" env-refresh.py $ARGS database
echo "Timing: env-refresh.sh completed in $(elapsed_ms "$SCRIPT_START_MS")ms"
