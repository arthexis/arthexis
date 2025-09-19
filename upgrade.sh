#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"

FORCE=0
CLEAN=0
NO_RESTART=0
CANARY=0
REVERT=0
NO_WARN=0
FAILOVER_BRANCH_CREATED=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --latest)
      FORCE=1
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
    --canary)
      CANARY=1
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
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

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
    else
      echo "Failed to delete failover branch $branch" >&2
    fi
  done
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

trap 'status=$?; if [[ $status -eq 0 ]]; then cleanup_failover_branches; fi' EXIT

if [[ $REVERT -eq 1 ]]; then
  latest=$(git for-each-ref --format='%(refname:short)' refs/heads/failover-* | sort | tail -n 1)
  if [ -z "$latest" ]; then
    echo "No failover branches found." >&2
    exit 1
  fi
  if git cat-file -e "$latest:db.sqlite3" 2>/dev/null; then
    current_kb=0
    [ -f db.sqlite3 ] && current_kb=$(du -k db.sqlite3 | cut -f1)
    prev_bytes=$(git cat-file -s "$latest:db.sqlite3")
    prev_kb=$(((prev_bytes + 1023) / 1024))
    if [ "$current_kb" -ne "$prev_kb" ]; then
      diff=$((current_kb - prev_kb))
      [ $diff -lt 0 ] && diff=$(( -diff ))
      echo "Warning: reverting will replace database (current ${current_kb}KB vs failover ${prev_kb}KB; diff ${diff}KB)"
      read -r -p "Proceed? [y/N]: " resp
      if [[ ! $resp =~ ^[Yy]$ ]]; then
        echo "Revert cancelled."
        exit 1
      fi
    fi
  fi
  echo "Stashing current changes..." >&2
  git stash push -u -m "upgrade-revert $(date -Is)" >/dev/null || true
  echo "Reverting to $latest"
  git reset --hard "$latest"
  if git cat-file -e "$latest:db.sqlite3" 2>/dev/null; then
    git show "$latest:db.sqlite3" > db.sqlite3
  fi
  exit 0
fi

# Run in canary mode if requested
if [[ $CANARY -eq 1 ]]; then
  echo "Running canary upgrade test in Docker..."
  docker build -t arthexis-canary -f Dockerfile.canary .
  docker run --rm arthexis-canary
  exit $?
fi

# Determine current and remote versions
BRANCH=$(git rev-parse --abbrev-ref HEAD)
LOCAL_VERSION="0"
[ -f VERSION ] && LOCAL_VERSION=$(cat VERSION)

echo "Checking repository for updates..."
git fetch origin "$BRANCH"
REMOTE_VERSION="$LOCAL_VERSION"
if git cat-file -e "origin/$BRANCH:VERSION" 2>/dev/null; then
  REMOTE_VERSION=$(git show "origin/$BRANCH:VERSION" | tr -d '\r')
fi

if [[ $FORCE -ne 1 && "$LOCAL_VERSION" == "$REMOTE_VERSION" ]]; then
  echo "Already up-to-date (version $LOCAL_VERSION)"
  exit 0
fi

create_failover_branch

# Track if the node is installed (virtual environment present)
VENV_PRESENT=1
[ -d .venv ] || VENV_PRESENT=0

# Stop running instance only if the node is installed
if [[ $NO_RESTART -eq 0 && $VENV_PRESENT -eq 1 ]]; then
  echo "Stopping running instance..."
  ./stop.sh --all >/dev/null 2>&1 || true
fi

# Pull latest changes
echo "Pulling latest changes..."
git pull --rebase

# Exit after pulling if the node isn't installed
if [ $VENV_PRESENT -eq 0 ]; then
  echo "Virtual environment not found. Run ./install.sh to install the node. Skipping remaining steps." >&2
  exit 0
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
if [[ $FORCE -eq 1 ]]; then
  ENV_ARGS="--latest"
fi
echo "Refreshing environment..."
FAILOVER_CREATED=1 ./env-refresh.sh $ENV_ARGS

# Reload personal user data fixtures

# Migrate existing systemd unit to dedicated Celery services if needed
LOCK_DIR="$BASE_DIR/locks"
if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE_NAME="$(cat "$LOCK_DIR/service.lck")"
  SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
  if [ -f "$SERVICE_FILE" ] && grep -Fq "celery -A" "$SERVICE_FILE"; then
    echo "Migrating service configuration for Celery..."
    MODE="internal"
    if [ -f "$LOCK_DIR/nginx_mode.lck" ]; then
      MODE="$(cat "$LOCK_DIR/nginx_mode.lck")"
    fi
    if [ "$MODE" = "public" ]; then
      PORT=8000
    else
      PORT=8888
    fi
    EXEC_CMD="$BASE_DIR/.venv/bin/python manage.py runserver 0.0.0.0:$PORT"
    sudo bash -c "cat > '$SERVICE_FILE'" <<SERVICEEOF
[Unit]
Description=Arthexis Constellation Django service
After=network.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
EnvironmentFile=-$BASE_DIR/redis.env
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
    CELERY_SERVICE_FILE="/etc/systemd/system/${CELERY_SERVICE}.service"
    CELERY_BEAT_SERVICE_FILE="/etc/systemd/system/${CELERY_BEAT_SERVICE}.service"
    sudo bash -c "cat > '$CELERY_SERVICE_FILE'" <<CELERYSERVICEEOF
[Unit]
Description=Celery Worker for $SERVICE_NAME
After=network.target redis.service

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
EnvironmentFile=-$BASE_DIR/redis.env
ExecStart=$BASE_DIR/.venv/bin/celery -A config worker -l info
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

# Ensure wlan1 refresh service uses new script location
WLAN1_REFRESH_SCRIPT="$BASE_DIR/scripts/wlan1-refresh.sh"
if [ -f /etc/systemd/system/wlan1-device-refresh.service ]; then
  echo "Migrating wlan1 refresh service to new name/location..."
  sudo systemctl stop wlan1-device-refresh || true
  sudo systemctl disable wlan1-device-refresh || true
  sudo rm -f /etc/systemd/system/wlan1-device-refresh.service
  sudo systemctl daemon-reload
fi
if [ -f "$WLAN1_REFRESH_SCRIPT" ]; then
  WLAN1_REFRESH_SERVICE_FILE="/etc/systemd/system/wlan1-refresh.service"
  cat <<EOF | sudo tee "$WLAN1_REFRESH_SERVICE_FILE" >/dev/null
[Unit]
Description=Refresh wlan1 MAC addresses in NetworkManager
After=NetworkManager.service

[Service]
Type=oneshot
ExecStart=$WLAN1_REFRESH_SCRIPT

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable wlan1-refresh >/dev/null 2>&1 || true
  "$WLAN1_REFRESH_SCRIPT" || true
fi

if [[ $NO_RESTART -eq 0 ]]; then
  echo "Restarting services..."
  if [ -f "$LOCK_DIR/service.lck" ]; then
    SERVICE_NAME="$(cat "$LOCK_DIR/service.lck")"
    echo "Existing services before restart:"
    systemctl status "$SERVICE_NAME" --no-pager || true
    if [ -f "$LOCK_DIR/celery.lck" ]; then
      systemctl status "celery-$SERVICE_NAME" --no-pager || true
      systemctl status "celery-beat-$SERVICE_NAME" --no-pager || true
    fi
  fi
  nohup ./start.sh >/dev/null 2>&1 &
  echo "Services restart triggered"
fi
