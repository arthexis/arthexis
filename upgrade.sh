#!/usr/bin/env bash
set -eE

# Initialize logging and helper functions shared across upgrade steps.
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
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
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
# Capture stdout/stderr to a timestamped log for later review.
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE")
exec 2> >(tee "$LOG_FILE" >&2)
cd "$BASE_DIR"

LOCK_DIR="$BASE_DIR/locks"
SYSTEMD_UNITS_LOCK="$LOCK_DIR/systemd_services.lck"
SERVICE_NAME=""
[ -f "$LOCK_DIR/service.lck" ] && SERVICE_NAME="$(cat "$LOCK_DIR/service.lck")"

queue_startup_net_message() {
  python - "$BASE_DIR" <<'PY'
import sys
from pathlib import Path

from nodes.startup_notifications import queue_startup_message

base_dir = Path(sys.argv[1])
queue_startup_message(base_dir=base_dir)
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

  case "$role" in
    Control|Constellation|Watchtower)
      ;;
    *)
      return
      ;;
  esac

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
  local new_hash=""
  local stored_hash=""

  if [ ! -f "$req_file" ]; then
    echo "requirements.txt not found; skipping dependency sync."
    return
  fi

  new_hash=$(md5sum "$req_file" | awk '{print $1}')
  if [ -f "$md5_file" ]; then
    stored_hash=$(cat "$md5_file")
  fi

  if [ "$new_hash" != "$stored_hash" ]; then
    if [ -f "$PIP_INSTALL_HELPER" ] && command -v python >/dev/null 2>&1; then
      python "$PIP_INSTALL_HELPER" -r "$req_file"
    else
      pip install -r "$req_file"
    fi
    echo "$new_hash" > "$md5_file"
  else
    echo "Requirements unchanged. Skipping installation."
  fi
}

auto_realign_branch_for_role() {
  local role="$1"
  local branch="$2"

  case "$role" in
    Control|Constellation|Watchtower)
      ;;
    *)
      return
      ;;
  esac

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
NO_WARN=0
LOCAL_ONLY=0
# Parse CLI options controlling the upgrade strategy.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --latest|--unstable)
      CHANNEL="unstable"
      shift
      ;;
    --force)
      FORCE_STOP=1
      FORCE_UPGRADE=1
      shift
      ;;
    --clean)
      CLEAN=1
      shift
      ;;
    --no-restart)
      NO_RESTART=1
      shift
      ;;
    --no-warn)
      NO_WARN=1
      shift
      ;;
    --local)
      LOCAL_ONLY=1
      shift
      ;;
    --stable|--normal|--regular)
      CHANNEL="stable"
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

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
if [ -f "$UPGRADE_RERUN_LOCK" ]; then
  RERUN_AFTER_SELF_UPDATE=1
  RERUN_TARGET_VERSION=$(tr -d '\r\n' < "$UPGRADE_RERUN_LOCK")
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
      if arthexis_lcd_feature_enabled "$LOCK_DIR" && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
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
      if arthexis_lcd_feature_enabled "$LOCK_DIR" && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
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
    if arthexis_lcd_feature_enabled "$LOCK_DIR" && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
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

upgrade_failure_recovery() {
  local exit_code=$?

  trap - ERR
  set +e

  echo "Upgrade failed with exit code ${exit_code}; attempting to restore services..." >&2

  if [[ $NO_RESTART -eq 1 ]]; then
    echo "Automatic recovery skipped because --no-restart was provided." >&2
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
    echo "Please create or select a branch and rerun the upgrade." >&2
    exit 1
  fi

  if git show-ref --verify --quiet "refs/heads/$TARGET_BRANCH"; then
    git switch "$TARGET_BRANCH" >/dev/null
  else
    git switch -c "$TARGET_BRANCH" "origin/$TARGET_BRANCH" >/dev/null
  fi

  BRANCH="$TARGET_BRANCH"
  echo "Switched to branch $BRANCH." >&2
fi
LOCAL_VERSION="0"
[ -f VERSION ] && LOCAL_VERSION=$(tr -d '\r\n' < VERSION)

REMOTE_VERSION="$LOCAL_VERSION"
if [[ $LOCAL_ONLY -eq 1 ]]; then
  echo "Local refresh requested; skipping remote update check."
else
  echo "Checking repository for updates..."
  git fetch origin "$BRANCH"
  if git cat-file -e "origin/$BRANCH:VERSION" 2>/dev/null; then
    REMOTE_VERSION=$(git show "origin/$BRANCH:VERSION" | tr -d '\r\n')
  fi
fi

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

# Stop running instance only if the node is installed
if [[ $VENV_PRESENT -eq 1 ]]; then
  echo "Stopping running instance..."
  STOP_ARGS=(--all)
  if [[ $FORCE_STOP -eq 1 ]]; then
    STOP_ARGS+=(--force)
  fi
  if ! ./stop.sh "${STOP_ARGS[@]}"; then
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
    printf '%s\n' "$REMOTE_VERSION" > "$UPGRADE_RERUN_LOCK"
    echo "upgrade.sh was updated during git pull; please run the upgrade again to use the new script." >&2
    exit "$UPGRADE_RERUN_EXIT_CODE"
  fi
fi

# Update the development marker to reflect the new revision.
arthexis_update_version_marker "$BASE_DIR"

# Exit after pulling if the node isn't installed
if [ $VENV_PRESENT -eq 0 ]; then
  echo "Virtual environment not found. Run ./install.sh to install the node. Skipping remaining steps." >&2
  exit 0
fi

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
  pip install --upgrade pip
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
  if arthexis_lcd_feature_enabled "$LOCK_DIR" && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
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

if [[ $NO_RESTART -eq 0 ]]; then
  if ! restart_services; then
    echo "Detected failed restart after upgrade." >&2
    echo "Manual intervention required to restore services." >&2
    exit 1
  fi
fi

if arthexis_lcd_feature_enabled "$LOCK_DIR"; then
  if ! queue_startup_net_message; then
    echo "Failed to queue startup Net Message" >&2
  fi
fi

arthexis_refresh_desktop_shortcuts "$BASE_DIR"
