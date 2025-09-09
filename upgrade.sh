#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"

FORCE=0
CLEAN=0
NO_RESTART=0
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
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

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

# Stash local changes if any
STASHED=0
if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
  echo "Warning: stashing local changes before upgrade" >&2
  git stash push -u -m "auto-upgrade $(date -Is)" >/dev/null || true
  STASHED=1
fi

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

# Restore stashed changes
if [ "$STASHED" -eq 1 ]; then
  echo "Restoring local changes..."
  git stash pop || true
fi

# Exit after pulling if the node isn't installed
if [ $VENV_PRESENT -eq 0 ]; then
  echo "Virtual environment not found. Run ./install.sh to install the node. Skipping remaining steps." >&2
  exit 0
fi

# Remove existing database if requested
if [ "$CLEAN" -eq 1 ]; then
  DB_FILE="db.sqlite3"
  if [ -f "$DB_FILE" ]; then
    BACKUP_DIR="$BASE_DIR/backups"
    mkdir -p "$BACKUP_DIR"
    cp "$DB_FILE" "$BACKUP_DIR/db.sqlite3.$(date +%Y%m%d%H%M%S).bak"
  fi
  rm -f "$DB_FILE"
fi

# Refresh environment and restart service
ENV_ARGS=""
if [[ $FORCE -eq 1 ]]; then
  ENV_ARGS="--latest"
fi
echo "Refreshing environment..."
./env-refresh.sh $ENV_ARGS

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
