#!/usr/bin/env bash
set -eE

# Initialize logging and helper functions shared across upgrade steps.
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
export TZ="${TZ:-America/Monterrey}"
PIP_INSTALL_HELPER="$BASE_DIR/scripts/helpers/pip_install.py"
# Track upgrade script changes triggered by git pull so the newer version can be re-run.
UPGRADE_SCRIPT_PATH="$BASE_DIR/upgrade.sh"
INITIAL_UPGRADE_HASH=""
UPGRADE_RERUN_EXIT_CODE=3
if [ -f "$UPGRADE_SCRIPT_PATH" ]; then
  INITIAL_UPGRADE_HASH="$(sha256sum "$UPGRADE_SCRIPT_PATH" | awk '{print $1}')"
fi
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
# Record upgrade lifecycle in the startup report for visibility in admin reports.
UPGRADE_SCRIPT_NAME="$(basename "$0")"
arthexis_log_startup_event "$BASE_DIR" "$UPGRADE_SCRIPT_NAME" "start" "invoked"

log_upgrade_exit() {
  local status=$?
  arthexis_log_startup_event "$BASE_DIR" "$UPGRADE_SCRIPT_NAME" "finish" "status=$status"
}
trap log_upgrade_exit EXIT
# shellcheck source=scripts/helpers/nginx_maintenance.sh
. "$BASE_DIR/scripts/helpers/nginx_maintenance.sh"
# shellcheck source=scripts/helpers/desktop_shortcuts.sh
. "$BASE_DIR/scripts/helpers/desktop_shortcuts.sh"
# shellcheck source=scripts/helpers/ports.sh
. "$BASE_DIR/scripts/helpers/ports.sh"
# shellcheck source=scripts/helpers/version_marker.sh
. "$BASE_DIR/scripts/helpers/version_marker.sh"
# shellcheck source=scripts/helpers/auto-upgrade-service.sh
. "$BASE_DIR/scripts/helpers/auto-upgrade-service.sh"
# shellcheck source=scripts/helpers/systemd_locks.sh
. "$BASE_DIR/scripts/helpers/systemd_locks.sh"
# shellcheck source=scripts/helpers/service_manager.sh
. "$BASE_DIR/scripts/helpers/service_manager.sh"
# shellcheck source=scripts/helpers/suite-uptime-lock.sh
. "$BASE_DIR/scripts/helpers/suite-uptime-lock.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
# Prefer python3 but fall back to python when only the legacy binary is available.
DEFAULT_VENV_PYTHON="$BASE_DIR/.venv/bin/python"
if [ -x "$DEFAULT_VENV_PYTHON" ]; then
  PYTHON_BIN="$DEFAULT_VENV_PYTHON"
else
  PYTHON_BIN="$(command -v python3 || command -v python || true)"
fi
# Capture stdout/stderr to a timestamped log for later review.
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE")
exec 2> >(tee "$LOG_FILE" >&2)
cd "$BASE_DIR"

LOCK_DIR="$BASE_DIR/locks"
SYSTEMD_UNITS_LOCK="$LOCK_DIR/systemd_services.lck"
SERVICE_NAME=""
[ -f "$LOCK_DIR/service.lck" ] && SERVICE_NAME="$(cat "$LOCK_DIR/service.lck")"

ensure_git_safe_directory() {
  if ! command -v git >/dev/null 2>&1; then
    return 0
  fi

  # Avoid fatal "dubious ownership" errors when upgrades run under systemd users.
  if git config --global --get-all safe.directory "$BASE_DIR" >/dev/null 2>&1; then
    return 0
  fi

  git config --global --add safe.directory "$BASE_DIR" >/dev/null 2>&1 || true
}

is_non_terminal_role() {
  case "$1" in
    Control|Constellation|Watchtower)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

pip_requires_break_system_packages() {
  local python_bin="$1"

  if [ -z "$python_bin" ] || ! command -v "$python_bin" >/dev/null 2>&1; then
    return 1
  fi

  # If we're already running inside a virtual environment, installing packages
  # won't touch system-managed locations, so we do not need the override.
  "$python_bin" - <<'PY'
import sys

if sys.prefix != sys.base_prefix:
    sys.exit(1)
PY

  "$python_bin" - <<'PY'
from pathlib import Path
import sys

version = f"python{sys.version_info.major}.{sys.version_info.minor}"
candidates = [
    Path(sys.base_prefix) / "EXTERNALLY-MANAGED",
    Path(sys.base_prefix) / "lib" / version / "EXTERNALLY-MANAGED",
]
sys.exit(0 if any(path.exists() for path in candidates) else 1)
PY
}

ensure_virtualenv() {
  local venv_dir="$BASE_DIR/.venv"
  local venv_python="$venv_dir/bin/python"
  local creator="$PYTHON_BIN"

  if [ -x "$venv_python" ]; then
    PYTHON_BIN="$venv_python"
    return 0
  fi

  if [ -z "$creator" ] || ! command -v "$creator" >/dev/null 2>&1; then
    creator="$(command -v python3 || command -v python || true)"
  fi

  if [ -z "$creator" ]; then
    echo "Python interpreter not found; cannot create virtual environment at $venv_dir." >&2
    return 1
  fi

  echo "Creating virtual environment at $venv_dir..."
  "$creator" -m venv "$venv_dir"

  if [ ! -x "$venv_python" ]; then
    echo "Failed to create virtual environment at $venv_dir." >&2
    return 1
  fi

  PYTHON_BIN="$venv_python"
  return 0
}

configure_nginx_site() {
  local setup_script="$BASE_DIR/nginx-setup.sh"

  if [ ! -x "$setup_script" ]; then
    return 0
  fi

  if ! arthexis_can_manage_nginx; then
    echo "Skipping nginx configuration; sudo privileges or nginx assets are unavailable." >&2
    return 0
  fi

  if ! "$setup_script"; then
    echo "Warning: failed to configure nginx via $setup_script" >&2
  fi
}

ensure_git_safe_directory

reset_safe_git_changes() {
  local role="${1:-Terminal}"

  # Discard known auto-generated files that should not block rebases.
  local safe_rebase_files=(
    "VERSION"
  )

  # Remove generated working directories that should stay untracked.
  local safe_generated_paths=(
    "cache"
  )

  # Restore tracked placeholders that may be removed by cleanup scripts.
  local safe_placeholder_files=(
    "logs/.gitkeep"
    "logs/old/.gitkeep"
  )

  if ! command -v git >/dev/null 2>&1; then
    return 0
  fi

  local generated_path
  for generated_path in "${safe_generated_paths[@]}"; do
    if [ -e "$generated_path" ]; then
      echo "Removing generated path $generated_path before upgrading..."
      rm -rf -- "$generated_path"
    fi
  done

  local placeholder
  for placeholder in "${safe_placeholder_files[@]}"; do
    if git ls-files --error-unmatch "$placeholder" >/dev/null 2>&1; then
      if [ ! -f "$placeholder" ] || ! git diff --quiet -- "$placeholder" 2>/dev/null; then
        echo "Restoring placeholder $placeholder before upgrading..."
        mkdir -p "$(dirname "$placeholder")"
        git checkout -- "$placeholder" 2>/dev/null || git restore "$placeholder" 2>/dev/null || true
      fi
    fi
  done

  local status_output
  if ! status_output=$(git status --porcelain 2>/dev/null); then
    return 0
  fi

  local reset_candidates=()
  while IFS= read -r status_line; do
    [[ -z "$status_line" ]] && continue

    local path
    path="${status_line:3}"
    path="${path%% -> *}"

    for safe_file in "${safe_rebase_files[@]}"; do
      if [ "$path" = "$safe_file" ]; then
        reset_candidates+=("$path")
        break
      fi
    done
  done <<< "$status_output"

  if [ ${#reset_candidates[@]} -gt 0 ]; then
    echo "Discarding local changes for safe-to-replace files: ${reset_candidates[*]}"
    git checkout -- "${reset_candidates[@]}" 2>/dev/null || \
      git restore "${reset_candidates[@]}" 2>/dev/null || true
  fi

  if git status --porcelain 2>/dev/null | grep -q '^[ MADRCU?]'; then
    if is_non_terminal_role "$role"; then
      echo "Non-terminal role $role detected unstashed changes; discarding local modifications before upgrading..."
      if ! git reset --hard HEAD >/dev/null 2>&1; then
        echo "Failed to discard local changes automatically; please commit or stash before upgrading." >&2
        exit 1
      fi

      if ! git clean -fd -e data/ >/dev/null 2>&1; then
        echo "Failed to remove untracked files automatically; please commit or stash before upgrading." >&2
        exit 1
      fi

      return 0
    fi

    echo "Uncommitted changes detected before upgrading. Dirty paths:" >&2
    git status --short >&2 || true
    echo "Please commit or stash before upgrading." >&2
    exit 1
  fi
}

fetch_branch_with_ref_repair() {
  local remote="$1"
  local branch="$2"
  local fetch_output=""

  if fetch_output=$(git fetch "$remote" "$branch" 2>&1); then
    if [ -n "$fetch_output" ]; then
      printf '%s\n' "$fetch_output"
    fi
    return 0
  fi

  if [ -n "$fetch_output" ]; then
    printf '%s\n' "$fetch_output" >&2
  fi

  if printf '%s\n' "$fetch_output" | grep -q "cannot lock ref 'refs/remotes/${remote}/${branch}'"; then
    echo "Detected stale remote-tracking ref for ${remote}/${branch}; pruning and retrying git fetch..." >&2
    git remote prune "$remote" >/dev/null 2>&1 || true
    git update-ref -d "refs/remotes/${remote}/${branch}" >/dev/null 2>&1 || true

    if fetch_output=$(git fetch "$remote" "$branch" 2>&1); then
      if [ -n "$fetch_output" ]; then
        printf '%s\n' "$fetch_output"
      fi
      return 0
    fi

    if [ -n "$fetch_output" ]; then
      printf '%s\n' "$fetch_output" >&2
    fi
  fi

  return 1
}

queue_startup_net_message() {
  if [ -z "$PYTHON_BIN" ]; then
    echo "Python interpreter not found; cannot queue startup notification." >&2
    return 1
  fi

  "$PYTHON_BIN" - "$BASE_DIR" <<'PY'
import sys
from pathlib import Path

from nodes.startup_notifications import queue_startup_message

base_dir = Path(sys.argv[1])
queue_startup_message(base_dir=base_dir)
PY
}

broadcast_upgrade_start_net_message() {
  local local_revision="$1"
  local remote_revision="$2"

  if [ -z "$PYTHON_BIN" ]; then
    return 0
  fi

  "$PYTHON_BIN" - "$BASE_DIR" "$local_revision" "$remote_revision" <<'PY'
import os
import sys
from pathlib import Path

base_dir = Path(sys.argv[1])
local_rev = sys.argv[2] or None
remote_rev = sys.argv[3] or None

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
sys.path.insert(0, str(base_dir))

try:
    import django
    django.setup()
except Exception:
    sys.exit(0)

try:
    from core.tasks import _broadcast_upgrade_start_message
except Exception:
    sys.exit(0)

try:
    _broadcast_upgrade_start_message(local_rev, remote_rev)
except Exception:
    sys.exit(1)
PY
}
SERVICE_MANAGEMENT_MODE="$(arthexis_detect_service_mode "$LOCK_DIR")"
UPGRADE_IN_PROGRESS_LOCK="$LOCK_DIR/upgrade_in_progress.lck"
# Discover managed service if not explicitly recorded.
if [ -z "$SERVICE_NAME" ]; then
  while IFS= read -r unit_name; do
    case "$unit_name" in
      *-upgrade-guard.service|*-upgrade-guard.timer|celery-*.service|celery-beat-*.service|lcd-*.service)
        continue
        ;;
    esac

    if [[ "$unit_name" == *.service ]]; then
      SERVICE_NAME="${unit_name%.service}"
      break
    fi
  done < <(arthexis_read_systemd_unit_records "$LOCK_DIR")
fi

if [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_EMBEDDED" ]; then
  if [ -n "$SERVICE_NAME" ]; then
    arthexis_remove_celery_unit_stack "$LOCK_DIR" "$SERVICE_NAME"
    arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "lcd-${SERVICE_NAME}.service"
  fi
  if [ -f "$SYSTEMD_UNITS_LOCK" ]; then
    while IFS= read -r recorded_unit; do
      case "$recorded_unit" in
        celery-*.service|celery-beat-*.service)
          arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "$recorded_unit"
          ;;
        lcd-*.service)
          arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "$recorded_unit"
          ;;
      esac
    done < "$SYSTEMD_UNITS_LOCK"
  fi
fi

SYSTEMCTL_CMD=()
if command -v systemctl >/dev/null 2>&1; then
  SYSTEMCTL_CMD=(systemctl)
  if command -v sudo >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null; then
      SYSTEMCTL_CMD=(sudo -n systemctl)
    else
      SYSTEMCTL_CMD=(systemctl)
    fi
  fi
fi

# Capture sudo/systemd locations for environments where the defaults are missing.
SUDO_CMD=(sudo)
if ! command -v sudo >/dev/null 2>&1; then
  SUDO_CMD=()
fi

SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"

lcd_systemd_unit_present() {
  local service_name="$1"

  if [ -z "$service_name" ] || [ "$SERVICE_MANAGEMENT_MODE" != "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
    return 1
  fi

  local lcd_unit
  lcd_unit="lcd-${service_name}.service"

  if [ -f "${SYSTEMD_DIR}/${lcd_unit}" ]; then
    return 0
  fi

  if command -v systemctl >/dev/null 2>&1; then
    systemctl list-unit-files | awk '{print $1}' | grep -Fxq "$lcd_unit"
    return $?
  fi

  return 1
}

# Repair any auto-upgrade working directory to keep services consistent before modifying systemd.
if [ -n "$SERVICE_NAME" ]; then
  arthexis_repair_auto_upgrade_workdir "$BASE_DIR" "$SERVICE_NAME" "$SYSTEMD_DIR"
fi

# Remove deprecated systemd prestart environment refresh hooks before starting services.
remove_prestart_env_refresh() {
  local service="$1"

  if [ -z "$service" ]; then
    return 0
  fi

  local service_file="${SYSTEMD_DIR}/${service}.service"
  local refresh_pattern="^ExecStartPre=.*/scripts/prestart-refresh\\.sh$"

  if [ ! -f "$service_file" ]; then
    return 0
  fi

  if grep -Eq "$refresh_pattern" "$service_file"; then
    if [ ${#SUDO_CMD[@]} -gt 0 ]; then
      "${SUDO_CMD[@]}" sed -i "\~${refresh_pattern}~d" "$service_file"
    else
      sed -i "\~${refresh_pattern}~d" "$service_file"
    fi

    if [ ${#SYSTEMCTL_CMD[@]} -gt 0 ]; then
      "${SYSTEMCTL_CMD[@]}" daemon-reload >/dev/null 2>&1 || true
    fi

    echo "Removed deprecated prestart environment refresh from ${service}.service."
  fi
}

determine_node_role() {
  if [ -n "${NODE_ROLE:-}" ]; then
    echo "$NODE_ROLE"
    return
  fi

  local role_file="$LOCK_DIR/role.lck"
  if [ -f "$role_file" ]; then
    local role
    role=$(tr -d '\r\n' < "$role_file")
    if [ -n "$role" ]; then
      echo "$role"
      return
    fi
  fi

  echo "Terminal"
}

env_refresh_in_progress() {
  if ! command -v pgrep >/dev/null 2>&1; then
    return 1
  fi

  if pgrep -f "env-refresh.sh" >/dev/null 2>&1; then
    return 0
  fi

  if pgrep -f "env-refresh.py" >/dev/null 2>&1; then
    return 0
  fi

  return 1
}

parse_major_minor() {
  local version="$1"
  if [[ "$version" =~ ^[[:space:]]*([0-9]+)\.([0-9]+) ]]; then
    echo "${BASH_REMATCH[1]}.${BASH_REMATCH[2]}"
  fi
}

shares_stable_series() {
  local local_version
  local remote_version
  local_version=$(parse_major_minor "$1")
  remote_version=$(parse_major_minor "$2")

  if [ -z "$local_version" ] || [ -z "$remote_version" ]; then
    return 1
  fi

  [[ "$local_version" == "$remote_version" ]]
}

cleanup_non_terminal_git_state() {
  local role="$1"

  if ! is_non_terminal_role "$role"; then
    return
  fi

  if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
    echo "Detected interrupted rebase; aborting before continuing upgrade..."
    git rebase --abort >/dev/null 2>&1 || true
  fi

  if [ -f .git/MERGE_HEAD ]; then
    echo "Detected interrupted merge; aborting before continuing upgrade..."
    git merge --abort >/dev/null 2>&1 || git reset --merge >/dev/null 2>&1 || true
  fi

  if [ -f .git/CHERRY_PICK_HEAD ]; then
    echo "Detected interrupted cherry-pick; aborting before continuing upgrade..."
    git cherry-pick --abort >/dev/null 2>&1 || true
  fi
}

install_requirements_if_changed() {
  local req_file="$BASE_DIR/requirements.txt"
  local md5_file="$BASE_DIR/requirements.md5"
  local venv_python="$BASE_DIR/.venv/bin/python"
  local new_hash=""
  local stored_hash=""

  if [ ! -f "$req_file" ]; then
    echo "requirements.txt not found; skipping dependency sync."
    return
  fi

  if ! ensure_virtualenv; then
    echo "Virtual environment Python not found; run ./install.sh before upgrading dependencies." >&2
    return 1
  fi

  local python_bin="$venv_python"

  new_hash=$(md5sum "$req_file" | awk '{print $1}')
  if [ -f "$md5_file" ]; then
    stored_hash=$(cat "$md5_file")
  fi

  if [ "$new_hash" != "$stored_hash" ]; then
    if [ -f "$PIP_INSTALL_HELPER" ]; then
      "$python_bin" "$PIP_INSTALL_HELPER" -r "$req_file"
    else
      "$python_bin" -m pip install -r "$req_file"
    fi
    echo "$new_hash" > "$md5_file"
  else
    echo "Requirements unchanged. Skipping installation."
  fi
}

auto_realign_branch_for_role() {
  local role="$1"
  local branch="$2"

  if ! is_non_terminal_role "$role"; then
    return
  fi

  local behind=0 ahead=0
  if read -r behind ahead < <(git rev-list --left-right --count "origin/$branch...HEAD" 2>/dev/null); then
    :
  else
    behind=0
    ahead=0
  fi

  local dirty=0
  if ! git diff --quiet || ! git diff --cached --quiet; then
    dirty=1
  fi

  local has_untracked=0
  if [ -n "$(git ls-files --others --exclude-standard)" ]; then
    has_untracked=1
  fi

  if (( ahead > 0 )); then
    echo "Node role $role does not keep local commits; discarding $ahead local commit(s) to match origin/$branch..."
    git reset --hard "origin/$branch"
  elif (( dirty )); then
    echo "Discarding local working tree changes for $role node before pulling updates..."
    git reset --hard
  fi

  if (( has_untracked == 1 )); then
    echo "Removing untracked files for $role node before pulling updates (preserving data/)..."
    git clean -fd -e data/
  fi
}

CHANNEL="stable"
FORCE_STOP=0
FORCE_UPGRADE=0
CLEAN=0
NO_RESTART=0
FORCE_START=0
NO_WARN=0
LOCAL_ONLY=0
DETACHED=0
REQUESTED_BRANCH=""
FORWARDED_ARGS=()
# Parse CLI options controlling the upgrade strategy.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --latest|--unstable)
      CHANNEL="unstable"
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --force)
      FORCE_STOP=1
      FORCE_UPGRADE=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --clean)
      CLEAN=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --no-start|--no-restart)
      NO_RESTART=1
      FORCE_START=0
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --start)
      FORCE_START=1
      NO_RESTART=0
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --no-warn)
      NO_WARN=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --local)
      LOCAL_ONLY=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --detached)
      DETACHED=1
      shift
      ;;
    --branch)
      if [[ -z "${2:-}" ]]; then
        echo "--branch requires an argument" >&2
        exit 1
      fi

      REQUESTED_BRANCH="$2"
      FORWARDED_ARGS+=("$1" "$2")
      shift 2
      ;;
    --stable|--normal|--regular)
      CHANNEL="stable"
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

run_detached_upgrade() {
  local delegated_script="$BASE_DIR/delegated-upgrade.sh"

  if [ ! -x "$delegated_script" ]; then
    echo "Detached upgrades require $delegated_script" >&2
    exit 1
  fi

  local upgrade_cmd=("$UPGRADE_SCRIPT_PATH")
  if [ ${#FORWARDED_ARGS[@]} -gt 0 ]; then
    upgrade_cmd+=("${FORWARDED_ARGS[@]}")
  fi

  echo "Launching detached upgrade via $delegated_script..."
  "$delegated_script" "${upgrade_cmd[@]}"
  exit $?
}

if (( DETACHED )); then
  run_detached_upgrade
fi

mkdir -p "$LOCK_DIR"

# Mark upgrade progress so status.sh can surface active runs.
printf "%s\n" "$(date -Iseconds)" > "$UPGRADE_IN_PROGRESS_LOCK"
cleanup_upgrade_progress_lock() {
  rm -f "$UPGRADE_IN_PROGRESS_LOCK"
}
trap cleanup_upgrade_progress_lock EXIT INT TERM

UPGRADE_RERUN_LOCK="$LOCK_DIR/upgrade_rerun_required.lck"
RERUN_AFTER_SELF_UPDATE=0
RERUN_TARGET_VERSION=""
RERUN_SERVICE_WAS_ACTIVE=0
RERUN_LCD_WAS_ACTIVE=0
if [ -f "$UPGRADE_RERUN_LOCK" ]; then
  RERUN_AFTER_SELF_UPDATE=1
  while IFS= read -r rerun_line; do
    case "$rerun_line" in
      REMOTE_VERSION=*)
        RERUN_TARGET_VERSION="${rerun_line#REMOTE_VERSION=}"
        ;;
      SERVICE_WAS_ACTIVE=*)
        RERUN_SERVICE_WAS_ACTIVE="${rerun_line#SERVICE_WAS_ACTIVE=}"
        ;;
      LCD_WAS_ACTIVE=*)
        RERUN_LCD_WAS_ACTIVE="${rerun_line#LCD_WAS_ACTIVE=}"
        ;;
      *)
        if [ -z "$RERUN_TARGET_VERSION" ]; then
          RERUN_TARGET_VERSION="$(printf '%s' "$rerun_line" | tr -d '\r\n')"
        fi
        ;;
    esac
  done < "$UPGRADE_RERUN_LOCK"
  rm -f "$UPGRADE_RERUN_LOCK"
fi

# Wait for systemd services to report healthy before proceeding.
print_service_diagnostics() {
  local service="$1"
  shift
  local -a systemctl_cmd=("$@")

  if [ -z "$service" ] || ! command -v systemctl >/dev/null 2>&1; then
    return
  fi

  local -a journalctl_cmd=(journalctl)
  if command -v sudo >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null; then
      journalctl_cmd=(sudo -n journalctl)
    fi
  fi

  echo "Diagnostics for $service:"
  "${systemctl_cmd[@]}" status "$service" --no-pager || true
  echo "Recent logs for $service:" >&2
  "${journalctl_cmd[@]}" -u "$service" -n 50 --no-pager || true
  echo "For more details, run:" >&2
  echo "  ${systemctl_cmd[*]} status $service" >&2
  echo "  ${journalctl_cmd[*]} -u $service -n 200 --since \"1 hour ago\"" >&2
}

wait_for_service_active() {
  local service="$1"
  local require_registered="${2:-0}"
  if [ -z "$service" ]; then
    return 0
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    return 0
  fi

  local -a systemctl_cmd=(systemctl)
  if command -v sudo >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null; then
      systemctl_cmd=(sudo -n systemctl)
    else
      systemctl_cmd=(systemctl)
    fi
  fi

  if ! "${systemctl_cmd[@]}" list-unit-files | grep -Fq "${service}.service"; then
    if [ "$require_registered" -eq 1 ]; then
      echo "Service $service is not registered with systemd." >&2
      return 1
    fi
    return 0
  fi

  local timeout="${ARTHEXIS_WAIT_FOR_ACTIVE_TIMEOUT:-60}"
  if [[ ! "$timeout" =~ ^[0-9]+$ ]] || [ "$timeout" -le 0 ]; then
    timeout=60
  fi
  local deadline=$((SECONDS + timeout))
  echo "Waiting for service $service to report active..."
  while (( SECONDS < deadline )); do
    local status
    status=$("${systemctl_cmd[@]}" is-active "$service" 2>/dev/null || true)
    case "$status" in
      active)
        echo "Service $service is active."
        return 0
        ;;
      failed)
        echo "Service $service reported failed status." >&2
        print_service_diagnostics "$service" "${systemctl_cmd[@]}"
        return 1
        ;;
    esac
    sleep 2
  done

  echo "Timed out waiting for service $service to become active." >&2
  print_service_diagnostics "$service" "${systemctl_cmd[@]}"
  return 1
}

service_was_active() {
  local service_name="$1"

  if [ -z "$service_name" ]; then
    return 1
  fi

  if [ ${#SYSTEMCTL_CMD[@]} -gt 0 ] && \
     "${SYSTEMCTL_CMD[@]}" list-unit-files | awk '{print $1}' | grep -Fxq "${service_name}.service"; then
    if "${SYSTEMCTL_CMD[@]}" is-active --quiet "$service_name"; then
      return 0
    fi
    return 1
  fi

  if pgrep -f "manage.py runserver" >/dev/null 2>&1; then
    return 0
  fi

  return 1
}

lcd_service_was_active() {
  local service_name="$1"

  if [ -z "$service_name" ]; then
    return 1
  fi

  if [ ${#SYSTEMCTL_CMD[@]} -gt 0 ] && \
     "${SYSTEMCTL_CMD[@]}" list-unit-files | awk '{print $1}' | grep -Fxq "lcd-${service_name}.service"; then
    if "${SYSTEMCTL_CMD[@]}" is-active --quiet "lcd-${service_name}"; then
      return 0
    fi
    return 1
  fi

  if pgrep -f "python -m core\\.lcd_screen" >/dev/null 2>&1; then
    return 0
  fi

  return 1
}

# Restart core, LCD, and Celery services while respecting systemd when available.
restart_services() {
  echo "Restarting services..."
  if [ -f "$LOCK_DIR/service.lck" ]; then
    local service_name
    service_name="$(cat "$LOCK_DIR/service.lck")"
    local env_refresh_running=0
    if env_refresh_in_progress; then
      env_refresh_running=1
    fi
    local restart_via_systemd=0
    local systemctl_available=0
    local -a systemctl_cmd=()
    if command -v systemctl >/dev/null 2>&1; then
      systemctl_available=1
      systemctl_cmd=(systemctl)
      if command -v sudo >/dev/null 2>&1; then
        if sudo -n true 2>/dev/null; then
          systemctl_cmd=(sudo -n systemctl)
        else
          systemctl_cmd=(systemctl)
        fi
      fi
      echo "Existing services before restart:"
      "${systemctl_cmd[@]}" status "$service_name" --no-pager || true
      if "${systemctl_cmd[@]}" is-active --quiet "$service_name"; then
        echo "Signaling $service_name to restart via systemd..."
        "${systemctl_cmd[@]}" kill --signal=TERM "$service_name" || true
        restart_via_systemd=1
      fi
      if lcd_systemd_unit_present "$service_name"; then
        local lcd_service="lcd-$service_name"
        if "${systemctl_cmd[@]}" is-active --quiet "$lcd_service"; then
          echo "Signaling $lcd_service for restart via systemd..."
          "${systemctl_cmd[@]}" kill --signal=TERM "$lcd_service" || true
        fi
      fi
    fi
    if [ "$restart_via_systemd" -eq 1 ]; then
      if ! wait_for_service_active "$service_name" 1; then
        echo "Service $service_name did not become active after restart." >&2
        return 1
      fi
      if lcd_systemd_unit_present "$service_name"; then
        local lcd_service="lcd-$service_name"
        if ! wait_for_service_active "$lcd_service" 1; then
          if [ "$systemctl_available" -eq 1 ]; then
            echo "LCD service $lcd_service did not become active after restart. Attempting manual start..." >&2
            if "${systemctl_cmd[@]}" start "$lcd_service"; then
              if ! wait_for_service_active "$lcd_service" 1; then
                echo "LCD service $lcd_service did not become active after manual start." >&2
                return 1
              fi
            else
              echo "LCD service $lcd_service failed to start manually." >&2
              return 1
            fi
          else
            echo "LCD service $lcd_service did not become active after restart, and systemctl is unavailable for manual start." >&2
            return 1
          fi
        fi
      fi
      if [ -f "$LOCK_DIR/celery.lck" ] && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
        local celery_service="celery-$service_name"
        local celery_beat_service="celery-beat-$service_name"

        if ! wait_for_service_active "$celery_service" 1; then
          if [ "$systemctl_available" -eq 1 ]; then
            echo "Celery service $celery_service did not become active after restart. Attempting manual start..." >&2
            if "${systemctl_cmd[@]}" start "$celery_service"; then
              if ! wait_for_service_active "$celery_service" 1; then
                echo "Celery service $celery_service did not become active after manual start." >&2
                return 1
              fi
            else
              echo "Celery service $celery_service failed to start manually." >&2
              return 1
            fi
          else
            echo "Celery service $celery_service did not become active after restart, and systemctl is unavailable for manual start." >&2
            return 1
          fi
        fi

        if ! wait_for_service_active "$celery_beat_service" 1; then
          if [ "$systemctl_available" -eq 1 ]; then
            echo "Celery beat service $celery_beat_service did not become active after restart. Attempting manual start..." >&2
            if "${systemctl_cmd[@]}" start "$celery_beat_service"; then
              if ! wait_for_service_active "$celery_beat_service" 1; then
                echo "Celery beat service $celery_beat_service did not become active after manual start." >&2
                return 1
              fi
            else
              echo "Celery beat service $celery_beat_service failed to start manually." >&2
              return 1
            fi
          else
            echo "Celery beat service $celery_beat_service did not become active after restart, and systemctl is unavailable for manual start." >&2
            return 1
          fi
        fi
      fi
      return 0
    fi
    if ! ./start.sh; then
      echo "Service restart command failed." >&2
      return 1
    fi
    if ! wait_for_service_active "$service_name"; then
      echo "Service $service_name did not become active after restart." >&2
      return 1
    fi
    if lcd_systemd_unit_present "$service_name"; then
      local lcd_service="lcd-$service_name"
      if ! wait_for_service_active "$lcd_service" 1; then
        echo "LCD service $lcd_service did not become active after restart." >&2
        return 1
      fi
    fi
    return 0
  fi

  nohup ./start.sh >/dev/null 2>&1 &
  echo "Services restart triggered"
  return 0
}

restart_lcd_service() {
  local service_name="$1"

  if [ -z "$service_name" ]; then
    return 0
  fi

  if [ ${#SYSTEMCTL_CMD[@]} -eq 0 ] || [ "$SERVICE_MANAGEMENT_MODE" != "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
    return 0
  fi

  local lcd_service
  lcd_service="lcd-$service_name"

  if ! "${SYSTEMCTL_CMD[@]}" list-unit-files | awk '{print $1}' | grep -Fxq "${lcd_service}.service"; then
    return 0
  fi

  echo "Restarting LCD service ${lcd_service}..."
  "${SYSTEMCTL_CMD[@]}" restart "$lcd_service" || return 1
  wait_for_service_active "$lcd_service" 1
}

ensure_watchdog_running() {
  local service_name="$1"

  if [ -z "$service_name" ] || [ "$SERVICE_MANAGEMENT_MODE" != "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
    return 0
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    echo "Skipping watchdog start; systemctl is unavailable." >&2
    return 0
  fi

  echo "Ensuring watchdog service ${service_name}-watchdog is installed and running..."
  arthexis_install_watchdog_unit "$BASE_DIR" "$LOCK_DIR" "$service_name" "" "$SERVICE_MANAGEMENT_MODE"
}

upgrade_failure_recovery() {
  local exit_code=$?

  trap - ERR
  set +e

  echo "Upgrade failed with exit code ${exit_code}; attempting to restore services..." >&2

  if [[ $NO_RESTART -eq 1 ]]; then
    echo "Automatic recovery skipped because --no-start/--no-restart was provided." >&2
    exit "$exit_code"
  fi

  if [[ ${SERVICE_WAS_ACTIVE:-1} -eq 0 ]] && [[ $FORCE_START -eq 0 ]]; then
    echo "Automatic recovery skipped because services were stopped before the upgrade." >&2
    exit "$exit_code"
  fi

  if ! restart_services; then
    echo "Automatic recovery could not restore services; manual intervention required." >&2
  fi

  exit "$exit_code"
}

trap 'upgrade_failure_recovery' ERR

versions_share_minor() {
  local first="$1"
  local second="$2"

  local first_major=""
  local first_minor=""
  local second_major=""
  local second_minor=""

  if [[ $first =~ ^([0-9]+)\.([0-9]+) ]]; then
    first_major="${BASH_REMATCH[1]}"
    first_minor="${BASH_REMATCH[2]}"
  else
    return 1
  fi

  if [[ $second =~ ^([0-9]+)\.([0-9]+) ]]; then
    second_major="${BASH_REMATCH[1]}"
    second_minor="${BASH_REMATCH[2]}"
  else
    return 1
  fi

  if [[ $first_major == "$second_major" && $first_minor == "$second_minor" ]]; then
    return 0
  fi

  return 1
}

confirm_database_deletion() {
  local action="$1"
  local -a targets=()

  if [ -f "$BASE_DIR/db.sqlite3" ]; then
    targets+=("db.sqlite3")
  fi
  while IFS= read -r -d '' path; do
    targets+=("$(basename "$path")")
  done < <(find "$BASE_DIR" -maxdepth 1 -type f -name 'db_*.sqlite3' -print0 2>/dev/null)

  if [ ${#targets[@]} -eq 0 ] || [[ $NO_WARN -eq 1 ]]; then
    return 0
  fi

  echo "Warning: $action will delete the following database files without creating a backup:"
  local target
  for target in "${targets[@]}"; do
    echo "  - $target"
  done
  echo "Use --no-warn to bypass this prompt."
  local response
  read -r -p "Continue? [y/N] " response
  if [[ ! $response =~ ^[Yy]$ ]]; then
    return 1
  fi

  return 0
}

NODE_ROLE_NAME=$(determine_node_role)

cleanup_non_terminal_git_state "$NODE_ROLE_NAME"

# Determine current and remote versions
if [[ -n "$REQUESTED_BRANCH" ]]; then
  BRANCH="$REQUESTED_BRANCH"
  if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git switch "$BRANCH" >/dev/null
  elif git show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
    git switch -c "$BRANCH" "origin/$BRANCH" >/dev/null
  else
    echo "Requested branch $BRANCH not found locally or on origin; continuing without switching." >&2
  fi
else
  BRANCH=$(git rev-parse --abbrev-ref HEAD)
  if [[ "$BRANCH" == "HEAD" ]]; then
    echo "Detected detached HEAD; attempting to switch back to the tracked branch..." >&2

    determine_default_branch() {
      local remote_head
      remote_head=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)
      if [[ -n "$remote_head" ]]; then
        echo "${remote_head#origin/}"
        return 0
      fi

      git branch --remotes --contains HEAD 2>/dev/null \
        | sed -n 's#^[ *]*origin/##p' \
        | head -n1
    }

    TARGET_BRANCH=$(determine_default_branch)
    if [[ -z "$TARGET_BRANCH" ]]; then
      echo "Unable to determine branch to switch to while detached." >&2
      echo "Continuing in detached HEAD state; upgrade steps will run without switching branches." >&2
      BRANCH="HEAD"
    else
      if git show-ref --verify --quiet "refs/heads/$TARGET_BRANCH"; then
        git switch "$TARGET_BRANCH" >/dev/null
      else
        git switch -c "$TARGET_BRANCH" "origin/$TARGET_BRANCH" >/dev/null
      fi

      BRANCH="$TARGET_BRANCH"
      echo "Switched to branch $BRANCH." >&2
    fi
  fi
fi
LOCAL_VERSION="0"
[ -f VERSION ] && LOCAL_VERSION=$(tr -d '\r\n' < VERSION)
LOCAL_REVISION="$(git rev-parse HEAD 2>/dev/null || echo "")"

REMOTE_VERSION="$LOCAL_VERSION"
REMOTE_REVISION="$LOCAL_REVISION"
if [[ $LOCAL_ONLY -eq 1 ]]; then
  echo "Local refresh requested; skipping remote update check."
else
  reset_safe_git_changes "$NODE_ROLE_NAME"
  echo "Checking repository for updates..."
  fetch_branch_with_ref_repair origin "$BRANCH"
  REMOTE_REVISION="$(git rev-parse "origin/$BRANCH" 2>/dev/null || echo "$REMOTE_REVISION")"
  if git cat-file -e "origin/$BRANCH:VERSION" 2>/dev/null; then
    REMOTE_VERSION=$(git show "origin/$BRANCH:VERSION" | tr -d '\r\n')
  fi
fi

UPGRADE_PLANNED=1
if [[ "$LOCAL_VERSION" == "$REMOTE_VERSION" ]]; then
  if [[ $LOCAL_ONLY -eq 1 ]]; then
    echo "Proceeding with local refresh despite matching version $LOCAL_VERSION."
  elif [[ $RERUN_AFTER_SELF_UPDATE -eq 1 ]]; then
    echo "Detected prior upgrade.sh update; continuing upgrade for $REMOTE_VERSION despite matching versions."
  elif [[ "$CHANNEL" == "unstable" ]]; then
    echo "Unstable channel requested; continuing upgrade despite matching version $REMOTE_VERSION."
  elif [[ $FORCE_UPGRADE -eq 1 ]]; then
    echo "Forcing upgrade despite matching version $LOCAL_VERSION."
  else
    echo "Already on version $LOCAL_VERSION; skipping upgrade."
    exit 0
  fi
fi

auto_realign_branch_for_role "$NODE_ROLE_NAME" "$BRANCH"

# Track if the node is installed (virtual environment present)
VENV_PRESENT=1
[ -d .venv ] || VENV_PRESENT=0

SERVICE_WAS_ACTIVE=0
if service_was_active "$SERVICE_NAME"; then
  SERVICE_WAS_ACTIVE=1
elif [[ $RERUN_AFTER_SELF_UPDATE -eq 1 ]] && [[ ${RERUN_SERVICE_WAS_ACTIVE:-0} -eq 1 ]]; then
  SERVICE_WAS_ACTIVE=1
fi
LCD_WAS_ACTIVE=0
if lcd_service_was_active "$SERVICE_NAME"; then
  LCD_WAS_ACTIVE=1
elif [[ $RERUN_AFTER_SELF_UPDATE -eq 1 ]] && [[ ${RERUN_LCD_WAS_ACTIVE:-0} -eq 1 ]]; then
  LCD_WAS_ACTIVE=1
fi
LCD_RESTART_REQUIRED=$LCD_WAS_ACTIVE

if [[ $SERVICE_WAS_ACTIVE -eq 1 ]] && [[ $UPGRADE_PLANNED -eq 1 ]]; then
  if [[ -n "$LOCAL_REVISION" || -n "$REMOTE_REVISION" ]]; then
    if ! broadcast_upgrade_start_net_message "$LOCAL_REVISION" "$REMOTE_REVISION"; then
      echo "Warning: failed to broadcast upgrade Net Message" >&2
    fi
  fi
fi

# Stop running instance only if the node is installed
if [[ $VENV_PRESENT -eq 1 ]]; then
  echo "Stopping running instance..."
  STOP_ARGS=(--all)
  if [[ $FORCE_STOP -eq 1 ]]; then
    STOP_ARGS+=(--force)
  fi
  if ! ARTHEXIS_SKIP_LCD_STOP=1 ./stop.sh "${STOP_ARGS[@]}"; then
    if [[ $FORCE_STOP -eq 1 ]]; then
      echo "Upgrade aborted even after forcing stop. Resolve active charging sessions before retrying." >&2
    else
      echo "Upgrade aborted because active charging sessions are in progress. Resolve active charging sessions before retrying." >&2
    fi
    exit 1
  fi
  fi

# Pull latest changes
if [[ $LOCAL_ONLY -eq 1 ]]; then
  echo "Skipping git pull for local refresh."
else
  echo "Pulling latest changes..."
  git pull --rebase

  # If the upgrade script itself was updated, stop so the new version is executed on the next run.
  POST_PULL_UPGRADE_HASH=""
  if [ -f "$UPGRADE_SCRIPT_PATH" ]; then
    POST_PULL_UPGRADE_HASH="$(sha256sum "$UPGRADE_SCRIPT_PATH" | awk '{print $1}')"
  fi
  if [ -n "$INITIAL_UPGRADE_HASH" ] && [ -n "$POST_PULL_UPGRADE_HASH" ] && \
     [ "$POST_PULL_UPGRADE_HASH" != "$INITIAL_UPGRADE_HASH" ]; then
    {
      printf 'REMOTE_VERSION=%s\n' "$REMOTE_VERSION"
      printf 'SERVICE_WAS_ACTIVE=%s\n' "$SERVICE_WAS_ACTIVE"
      printf 'LCD_WAS_ACTIVE=%s\n' "$LCD_WAS_ACTIVE"
    } > "$UPGRADE_RERUN_LOCK"
    echo "upgrade.sh was updated during git pull; please run the upgrade again to use the new script." >&2
    exit "$UPGRADE_RERUN_EXIT_CODE"
  fi
fi

# Update the development marker to reflect the new revision.
arthexis_update_version_marker "$BASE_DIR"

# Create virtual environment automatically if missing
if [ $VENV_PRESENT -eq 0 ]; then
  if ensure_virtualenv; then
    VENV_PRESENT=1
  else
    echo "Virtual environment not found and automatic creation failed. Run ./install.sh to install the node." >&2
    exit 1
  fi
fi

configure_nginx_site

if arthexis_can_manage_nginx; then
  arthexis_refresh_nginx_maintenance "$BASE_DIR" \
    "/etc/nginx/sites-enabled/arthexis.conf" \
    "/etc/nginx/conf.d/arthexis-internal.conf" \
    "/etc/nginx/conf.d/arthexis-public.conf"
fi

# Ensure Python dependencies and database schema stay aligned with the
# freshly-pulled code before refreshing runtime data.
if [ $VENV_PRESENT -eq 1 ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHON_BIN="$VIRTUAL_ENV/bin/python"
  pip_install_env=()
  pip_install_flags=()
  if pip_requires_break_system_packages python; then
    pip_install_env+=("PIP_BREAK_SYSTEM_PACKAGES=1")
    pip_install_flags+=("--break-system-packages")
  fi
  env "${pip_install_env[@]}" python -m pip install --upgrade pip "${pip_install_flags[@]}"
  install_requirements_if_changed
  python manage.py migrate --noinput
  if ls data/*.json >/dev/null 2>&1; then
    python manage.py loaddata data/*.json
  fi
  deactivate
fi

# Remove existing database if requested
if [ "$CLEAN" -eq 1 ]; then
  if ! confirm_database_deletion "Running upgrade with --clean"; then
    echo "Upgrade aborted by user."
    exit 1
  fi
  rm -f db.sqlite3
  rm -f db_*.sqlite3 2>/dev/null || true
fi

# Refresh environment and restart service
ENV_ARGS=""
if [[ "$CHANNEL" == "unstable" ]]; then
  ENV_ARGS="--latest"
fi
echo "Refreshing environment..."
FAILOVER_CREATED=1 ./env-refresh.sh $ENV_ARGS

if [ -n "$SERVICE_NAME" ]; then
  remove_prestart_env_refresh "$SERVICE_NAME"
  if [ -f "$LOCK_DIR/celery.lck" ] && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
    remove_prestart_env_refresh "celery-$SERVICE_NAME"
    remove_prestart_env_refresh "celery-beat-$SERVICE_NAME"
  fi
  if lcd_systemd_unit_present "$SERVICE_NAME"; then
    remove_prestart_env_refresh "lcd-$SERVICE_NAME"
  fi
fi

# Reload personal user data fixtures

# Migrate existing systemd unit to dedicated Celery services if needed
if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE_NAME="$(cat "$LOCK_DIR/service.lck")"
  SERVICE_FILE="${SYSTEMD_DIR}/${SERVICE_NAME}.service"
  if [ -f "$SERVICE_FILE" ] && grep -Fq "celery -A" "$SERVICE_FILE"; then
    echo "Migrating service configuration for Celery..."
    touch "$LOCK_DIR/celery.lck"
    arthexis_install_service_stack "$BASE_DIR" "$LOCK_DIR" "$SERVICE_NAME" true "$BASE_DIR/service-start.sh" "$SERVICE_MANAGEMENT_MODE"
  fi
fi

if [ -n "$SERVICE_NAME" ] && lcd_systemd_unit_present "$SERVICE_NAME"; then
  arthexis_install_lcd_service_unit "$BASE_DIR" "$LOCK_DIR" "$SERVICE_NAME"
elif [ -n "$SERVICE_NAME" ] && [ "$NODE_ROLE_NAME" = "Control" ] && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
  arthexis_install_lcd_service_unit "$BASE_DIR" "$LOCK_DIR" "$SERVICE_NAME"
fi

SHOULD_RESTART_AFTER_UPGRADE=1
if [ -n "$SERVICE_NAME" ] && [[ $SERVICE_WAS_ACTIVE -eq 0 ]]; then
  SHOULD_RESTART_AFTER_UPGRADE=0
fi
if [[ $FORCE_START -eq 1 ]]; then
  SHOULD_RESTART_AFTER_UPGRADE=1
fi

if [[ $NO_RESTART -eq 0 ]]; then
  if [[ $SHOULD_RESTART_AFTER_UPGRADE -eq 0 ]]; then
    if [ -n "$SERVICE_NAME" ]; then
      echo "Service $SERVICE_NAME was not running before upgrade; skipping automatic restart."
    else
      echo "Skipping automatic restart because services were not running before upgrade."
    fi
  elif ! restart_services; then
    echo "Detected failed restart after upgrade." >&2
    echo "Manual intervention required to restore services." >&2
    exit 1
  else
    LCD_RESTART_REQUIRED=0
  fi
fi

if [ -n "$SERVICE_NAME" ] && [[ $NO_RESTART -eq 0 ]] && [[ $SHOULD_RESTART_AFTER_UPGRADE -eq 1 ]]; then
  ensure_watchdog_running "$SERVICE_NAME"
fi

if [ -n "$SERVICE_NAME" ] && [[ $NO_RESTART -eq 0 ]] && [[ $LCD_RESTART_REQUIRED -eq 1 ]]; then
  if ! restart_lcd_service "$SERVICE_NAME"; then
    echo "LCD service lcd-$SERVICE_NAME did not restart cleanly after upgrade." >&2
    exit 1
  fi
fi

if [ -n "$SERVICE_NAME" ] && [[ $NO_RESTART -eq 0 ]]; then
  arthexis_refresh_suite_uptime_lock "$BASE_DIR" || true
fi

if arthexis_lcd_feature_enabled "$LOCK_DIR"; then
  if ! queue_startup_net_message; then
    echo "Failed to queue startup Net Message" >&2
  fi
fi

arthexis_refresh_desktop_shortcuts "$BASE_DIR"
