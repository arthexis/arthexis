#!/usr/bin/env bash
set -e

# Initialize logging and helper functions shared across upgrade steps.
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# Track upgrade script changes triggered by git pull so the newer version can be re-run.
UPGRADE_SCRIPT_PATH="$BASE_DIR/upgrade.sh"
INITIAL_UPGRADE_HASH=""
UPGRADE_RERUN_EXIT_CODE=3
if [ -f "$UPGRADE_SCRIPT_PATH" ]; then
  INITIAL_UPGRADE_HASH="$(sha256sum "$UPGRADE_SCRIPT_PATH" | awk '{print $1}')"
fi
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
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
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
# Capture stdout/stderr to a timestamped log for later review.
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE")
exec 2> >(tee "$LOG_FILE" >&2)
cd "$BASE_DIR"

LOCK_DIR="$BASE_DIR/locks"
SERVICE_NAME=""
[ -f "$LOCK_DIR/service.lck" ] && SERVICE_NAME="$(cat "$LOCK_DIR/service.lck")"
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

BACKUP_DIR="$BASE_DIR/backups"

LAST_FAILOVER_BRANCH=""

# Repair any auto-upgrade working directory to keep services consistent before modifying systemd.
if [ -n "$SERVICE_NAME" ]; then
  arthexis_repair_auto_upgrade_workdir "$BASE_DIR" "$SERVICE_NAME" "$SYSTEMD_DIR"
fi

# Ensure systemd services refresh environment variables before starting.
ensure_prestart_env_refresh() {
  local service="$1"

  if [ -z "$service" ]; then
    return 0
  fi

  local service_file="${SYSTEMD_DIR}/${service}.service"
  local refresh_line="ExecStartPre=${BASE_DIR}/scripts/prestart-refresh.sh"

  if [ ! -f "$service_file" ] || grep -Fq "$refresh_line" "$service_file"; then
    return 0
  fi

  if [ ${#SUDO_CMD[@]} -gt 0 ]; then
    "${SUDO_CMD[@]}" sed -i "/^ExecStart=/i ${refresh_line}" "$service_file"
  else
    sed -i "/^ExecStart=/i ${refresh_line}" "$service_file"
  fi

  if [ ${#SYSTEMCTL_CMD[@]} -gt 0 ]; then
    "${SYSTEMCTL_CMD[@]}" daemon-reload >/dev/null 2>&1 || true
  fi

  echo "Ensured ${service}.service refreshes the environment before starting."
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

role_uses_failover_branch() {
  local role="$1"

  case "$role" in
    Control|Satellite|Watchtower|Constellation)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
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

  if (( ahead > 0 )) && [ -n "$LAST_FAILOVER_BRANCH" ]; then
    echo "The discarded commits are preserved on $LAST_FAILOVER_BRANCH."
  fi
}

LATEST=0
FORCE_STOP=0
CLEAN=0
NO_RESTART=0
REVERT=0
NO_WARN=0
FAILOVER_BRANCH_CREATED=0
STABLE=0
# Parse CLI options controlling the upgrade strategy.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --latest)
      LATEST=1
      shift
      ;;
    --force)
      FORCE_STOP=1
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
    --revert)
      REVERT=1
      shift
      ;;
    --no-warn)
      NO_WARN=1
      shift
      ;;
    --stable)
      STABLE=1
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

mkdir -p "$LOCK_DIR"

if [[ $LATEST -eq 1 && $STABLE -eq 1 ]]; then
  echo "--stable cannot be used together with --latest." >&2
  exit 1
fi

# Helpers for creating and cleaning up failover branches and database backups.
backup_database_for_branch() {
  local branch="$1"
  local source="$BASE_DIR/db.sqlite3"
  local backup_path="$BACKUP_DIR/${branch}.sqlite3"

  if [ ! -f "$source" ]; then
    return
  fi

  if ! mkdir -p "$BACKUP_DIR"; then
    echo "Failed to create backup directory at $BACKUP_DIR" >&2
    return
  fi

  if cp -p "$source" "$backup_path"; then
    echo "Saved database backup to backups/${branch}.sqlite3"
  else
    echo "Failed to create database backup at $backup_path" >&2
  fi
}

remove_backup_for_branch() {
  local branch="$1"
  local backup_path="$BACKUP_DIR/${branch}.sqlite3"

  if [ -f "$backup_path" ]; then
    if rm -f "$backup_path"; then
      echo "Removed database backup backups/${branch}.sqlite3"
    else
      echo "Failed to remove database backup at $backup_path" >&2
    fi
  fi
}

create_failover_branch() {
  local date
  date=$(date +%Y%m%d)
  local i=1
  while git rev-parse --verify "failover-$date-$i" >/dev/null 2>&1; do
    i=$((i+1))
  done
  local branch="failover-$date-$i"
  if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
    git add -A
    local tree
    tree=$(git write-tree)
    local commit
    commit=$(printf "Failover backup %s" "$(date -Is)" | git commit-tree "$tree" -p HEAD)
    git branch "$branch" "$commit"
    git reset --hard HEAD
  else
    git branch "$branch"
  fi
  echo "Created failover branch $branch"
  LAST_FAILOVER_BRANCH="$branch"
  backup_database_for_branch "$branch"
  FAILOVER_BRANCH_CREATED=1
}

cleanup_failover_branches() {
  if [[ $FAILOVER_BRANCH_CREATED -ne 1 ]]; then
    return
  fi

  local -a failover_branches
  if ! readarray -t failover_branches < <(git for-each-ref --format='%(refname:short)' refs/heads/failover-* | sort); then
    echo "Failed to enumerate failover branches for cleanup." >&2
    return
  fi

  local total=${#failover_branches[@]}
  if (( total <= 1 )); then
    return
  fi

  local current_branch
  current_branch=$(git rev-parse --abbrev-ref HEAD)
  local keep_branch
  if [[ $current_branch == failover-* ]]; then
    keep_branch="$current_branch"
  else
    keep_branch="${failover_branches[$((total-1))]}"
  fi

  echo "Pruning older failover branches (keeping $keep_branch)..."
  local branch
  for branch in "${failover_branches[@]}"; do
    if [[ $branch == "$keep_branch" ]]; then
      continue
    fi
    if git branch -D "$branch" >/dev/null 2>&1; then
      echo "Deleted failover branch $branch"
      remove_backup_for_branch "$branch"
    else
      echo "Failed to delete failover branch $branch" >&2
    fi
  done
}

# Wait for systemd services to report healthy before proceeding.
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
        "${systemctl_cmd[@]}" status "$service" --no-pager || true
        return 1
        ;;
    esac
    sleep 2
  done

  echo "Timed out waiting for service $service to become active." >&2
  "${systemctl_cmd[@]}" status "$service" --no-pager || true
  return 1
}

# Restart core and LCD services while respecting systemd when available. Celery is managed by
# systemd unit dependencies and should not be restarted directly here.
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
    if command -v systemctl >/dev/null 2>&1; then
      local -a systemctl_cmd=(systemctl)
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
      if [ -f "$LOCK_DIR/lcd_screen.lck" ]; then
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
      if [ -f "$LOCK_DIR/lcd_screen.lck" ]; then
        local lcd_service="lcd-$service_name"
        if ! wait_for_service_active "$lcd_service" 1; then
          echo "LCD service $lcd_service did not become active after restart." >&2
          return 1
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
    if [ -f "$LOCK_DIR/lcd_screen.lck" ]; then
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

revert_after_failed_restart() {
  local branch="$LAST_FAILOVER_BRANCH"

  if [ -z "$branch" ]; then
    echo "No failover branch recorded; cannot revert automatically." >&2
    return 1
  fi

  echo "Reverting to failover branch $branch after failed restart..."
  if ! git reset --hard "$branch"; then
    echo "Failed to reset repository to $branch." >&2
    return 1
  fi
  arthexis_update_version_marker "$BASE_DIR"

  local backup_path="$BACKUP_DIR/${branch}.sqlite3"
  if [ -f "$backup_path" ]; then
    if cp "$backup_path" db.sqlite3; then
      echo "Restored database from backups/${branch}.sqlite3"
    else
      echo "Failed to restore database from $backup_path" >&2
    fi
  fi

  if ! restart_services; then
    echo "Restart after reverting to failover branch failed; manual intervention required." >&2
    return 1
  fi

  echo "Services restarted from failover branch $branch"
  return 0
}

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
  echo "You can use --revert later to restore from the most recent failover backup."
  echo "Use --no-warn to bypass this prompt."
  local response
  read -r -p "Continue? [y/N] " response
  if [[ ! $response =~ ^[Yy]$ ]]; then
    return 1
  fi

  return 0
}

trap 'status=$?; if [[ $status -eq 0 ]]; then cleanup_failover_branches; fi' EXIT

NODE_ROLE_NAME=$(determine_node_role)

cleanup_non_terminal_git_state "$NODE_ROLE_NAME"

if [[ $REVERT -eq 1 ]]; then
  latest=$(git for-each-ref --format='%(refname:short)' refs/heads/failover-* | sort | tail -n 1)
  if [ -z "$latest" ]; then
    echo "No failover branches found." >&2
    exit 1
  fi
  backup_file="$BACKUP_DIR/${latest}.sqlite3"
  revert_source=""
  revert_temp=""
  if [ -f "$backup_file" ]; then
    revert_source="$backup_file"
  elif git cat-file -e "$latest:db.sqlite3" 2>/dev/null; then
    revert_temp=$(mktemp)
    if git show "$latest:db.sqlite3" > "$revert_temp"; then
      revert_source="$revert_temp"
    else
      rm -f "$revert_temp"
      revert_temp=""
    fi
  fi
  if [ -n "$revert_source" ]; then
    current_kb=0
    [ -f db.sqlite3 ] && current_kb=$(du -k db.sqlite3 | cut -f1)
    prev_kb=$(du -k "$revert_source" | cut -f1)
    if [ "$current_kb" -ne "$prev_kb" ]; then
      diff=$((current_kb - prev_kb))
      [ $diff -lt 0 ] && diff=$(( -diff ))
      echo "Warning: reverting will replace database (current ${current_kb}KB vs failover ${prev_kb}KB; diff ${diff}KB)"
      read -r -p "Proceed? [y/N]: " resp
      if [[ ! $resp =~ ^[Yy]$ ]]; then
        echo "Revert cancelled."
        [ -n "$revert_temp" ] && rm -f "$revert_temp"
        exit 1
      fi
    fi
  else
    echo "No database backup found for $latest. The database will not be modified." >&2
  fi
  echo "Stashing current changes..." >&2
  git stash push -u -m "upgrade-revert $(date -Is)" >/dev/null || true
  echo "Reverting to $latest"
  git reset --hard "$latest"
  if [ -n "$revert_source" ]; then
    if cp "$revert_source" db.sqlite3; then
      echo "Restored database from ${revert_source##*/}"
    else
      echo "Failed to restore database from $revert_source" >&2
    fi
  fi
  [ -n "$revert_temp" ] && rm -f "$revert_temp"
  exit 0
fi

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
[ -f VERSION ] && LOCAL_VERSION=$(cat VERSION)

echo "Checking repository for updates..."
git fetch origin "$BRANCH"
REMOTE_VERSION="$LOCAL_VERSION"
if git cat-file -e "origin/$BRANCH:VERSION" 2>/dev/null; then
  REMOTE_VERSION=$(git show "origin/$BRANCH:VERSION" | tr -d '\r')
fi

if role_uses_failover_branch "$NODE_ROLE_NAME"; then
  create_failover_branch
else
  echo "Skipping failover branch creation for $NODE_ROLE_NAME node."
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
echo "Pulling latest changes..."
git pull --rebase

# If the upgrade script itself was updated, stop so the new version is executed on the next run.
POST_PULL_UPGRADE_HASH=""
if [ -f "$UPGRADE_SCRIPT_PATH" ]; then
  POST_PULL_UPGRADE_HASH="$(sha256sum "$UPGRADE_SCRIPT_PATH" | awk '{print $1}')"
fi
if [ -n "$INITIAL_UPGRADE_HASH" ] && [ -n "$POST_PULL_UPGRADE_HASH" ] && \
   [ "$POST_PULL_UPGRADE_HASH" != "$INITIAL_UPGRADE_HASH" ]; then
  echo "upgrade.sh was updated during git pull; please run the upgrade again to use the new script." >&2
  exit "$UPGRADE_RERUN_EXIT_CODE"
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
if [[ $LATEST -eq 1 ]]; then
  ENV_ARGS="--latest"
fi
echo "Refreshing environment..."
FAILOVER_CREATED=1 ./env-refresh.sh $ENV_ARGS

if [ -n "$SERVICE_NAME" ]; then
  ensure_prestart_env_refresh "$SERVICE_NAME"
  if [ -f "$LOCK_DIR/celery.lck" ]; then
    ensure_prestart_env_refresh "celery-$SERVICE_NAME"
    ensure_prestart_env_refresh "celery-beat-$SERVICE_NAME"
  fi
  if [ -f "$LOCK_DIR/lcd_screen.lck" ]; then
    ensure_prestart_env_refresh "lcd-$SERVICE_NAME"
  fi
fi

# Reload personal user data fixtures

# Migrate existing systemd unit to dedicated Celery services if needed
if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE_NAME="$(cat "$LOCK_DIR/service.lck")"
  SERVICE_FILE="${SYSTEMD_DIR}/${SERVICE_NAME}.service"
  if [ -f "$SERVICE_FILE" ] && grep -Fq "celery -A" "$SERVICE_FILE"; then
    echo "Migrating service configuration for Celery..."
    MODE="internal"
    if [ -f "$LOCK_DIR/nginx_mode.lck" ]; then
      MODE="$(cat "$LOCK_DIR/nginx_mode.lck")"
    fi
    PORT="$(arthexis_detect_backend_port "$BASE_DIR")"
    EXEC_CMD="$BASE_DIR/.venv/bin/python manage.py runserver 0.0.0.0:$PORT"
    sudo bash -c "cat > '$SERVICE_FILE'" <<SERVICEEOF
[Unit]
Description=Arthexis Constellation Django service
After=network.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
EnvironmentFile=-$BASE_DIR/redis.env
EnvironmentFile=-$BASE_DIR/debug.env
ExecStart=$EXEC_CMD
Restart=always
User=$(id -un)

[Install]
WantedBy=multi-user.target
SERVICEEOF
    # Ensure Celery units exist and are enabled
    touch "$LOCK_DIR/celery.lck"
    CELERY_SERVICE="celery-$SERVICE_NAME"
    CELERY_BEAT_SERVICE="celery-beat-$SERVICE_NAME"
    CELERY_SERVICE_FILE="${SYSTEMD_DIR}/${CELERY_SERVICE}.service"
    CELERY_BEAT_SERVICE_FILE="${SYSTEMD_DIR}/${CELERY_BEAT_SERVICE}.service"
    sudo bash -c "cat > '$CELERY_SERVICE_FILE'" <<CELERYSERVICEEOF
[Unit]
Description=Celery Worker for $SERVICE_NAME
After=network.target redis.service

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
EnvironmentFile=-$BASE_DIR/redis.env
EnvironmentFile=-$BASE_DIR/debug.env
ExecStart=$BASE_DIR/.venv/bin/celery -A config worker -l info --concurrency=2
Restart=always
User=$(id -un)

[Install]
WantedBy=multi-user.target
CELERYSERVICEEOF
    sudo bash -c "cat > '$CELERY_BEAT_SERVICE_FILE'" <<BEATSERVICEEOF
[Unit]
Description=Celery Beat for $SERVICE_NAME
After=network.target redis.service

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
EnvironmentFile=-$BASE_DIR/redis.env
EnvironmentFile=-$BASE_DIR/debug.env
ExecStart=$BASE_DIR/.venv/bin/celery -A config beat -l info
Restart=always
User=$(id -un)

[Install]
WantedBy=multi-user.target
BEATSERVICEEOF
    sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE_NAME" "$CELERY_SERVICE" "$CELERY_BEAT_SERVICE"
  fi
fi

if [[ $NO_RESTART -eq 0 ]]; then
  if ! restart_services; then
    echo "Detected failed restart after upgrade." >&2
    if revert_after_failed_restart; then
      echo "Upgrade reverted after restart failure. Manual verification recommended." >&2
    else
      echo "Automatic revert after restart failure was unsuccessful; manual intervention required." >&2
    fi
    exit 1
  fi
fi

arthexis_refresh_desktop_shortcuts "$BASE_DIR"
