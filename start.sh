#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"
LOCK_DIR="$BASE_DIR/locks"

# Ensure virtual environment is available
if [ ! -d .venv ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi
source .venv/bin/activate

# Load any .env files to configure environment variables
for env_file in *.env; do
  [ -f "$env_file" ] || continue
  set -a
  . "$env_file"
  set +a
done

# Collect static files only when their sources change
STATIC_MD5_FILE="$BASE_DIR/staticfiles.md5"
if ! STATIC_HASH=$(python scripts/staticfiles_md5.py); then
  echo "Failed to compute static files hash; running collectstatic."
  python manage.py collectstatic --noinput
else
  STORED_HASH=""
  [ -f "$STATIC_MD5_FILE" ] && STORED_HASH=$(cat "$STATIC_MD5_FILE")
  if [ "$STATIC_HASH" != "$STORED_HASH" ]; then
    if python manage.py collectstatic --noinput; then
      echo "$STATIC_HASH" > "$STATIC_MD5_FILE"
    else
      echo "collectstatic failed"
      exit 1
    fi
  else
    echo "Static files unchanged. Skipping collectstatic."
  fi
fi

# If a systemd service was installed, restart it instead of launching a new process
if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE_NAME="$(cat "$LOCK_DIR/service.lck")"
  if systemctl list-unit-files | grep -Fq "${SERVICE_NAME}.service"; then
    sudo systemctl restart "$SERVICE_NAME"
    # Show status information so the user can verify the service state
    sudo systemctl status "$SERVICE_NAME" --no-pager
    if [ -f "$LOCK_DIR/lcd_screen.lck" ]; then
      LCD_SERVICE="lcd-$SERVICE_NAME"
      if systemctl list-unit-files | grep -Fq "${LCD_SERVICE}.service"; then
        sudo systemctl restart "$LCD_SERVICE"
        sudo systemctl status "$LCD_SERVICE" --no-pager || true
      fi
    fi
    if [ -f "$LOCK_DIR/celery.lck" ]; then
      CELERY_SERVICE="celery-$SERVICE_NAME"
      CELERY_BEAT_SERVICE="celery-beat-$SERVICE_NAME"
      if systemctl list-unit-files | grep -Fq "${CELERY_SERVICE}.service"; then
        sudo systemctl restart "$CELERY_SERVICE"
        sudo systemctl status "$CELERY_SERVICE" --no-pager || true
      fi
      if systemctl list-unit-files | grep -Fq "${CELERY_BEAT_SERVICE}.service"; then
        sudo systemctl restart "$CELERY_BEAT_SERVICE"
        sudo systemctl status "$CELERY_BEAT_SERVICE" --no-pager || true
      fi
    fi
    exit 0
  fi
fi

# Determine default port based on nginx mode if present
PORT=""
RELOAD=false
# Celery workers process Post Office's email queue; enable by default.
CELERY=true
if [ -f "$LOCK_DIR/nginx_mode.lck" ]; then
  MODE="$(cat "$LOCK_DIR/nginx_mode.lck")"
else
  MODE="internal"
fi
PORT=8888

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="$2"
      shift 2
      ;;
    --reload)
      RELOAD=true
      shift
      ;;
    --celery)
      CELERY=true
      shift
      ;;
    --no-celery)
      CELERY=false
      shift
      ;;
    --public)
      PORT=8888
      shift
      ;;
    --internal)
      PORT=8888
      shift
      ;;
      *)
      echo "Usage: $0 [--port PORT] [--reload] [--public|--internal] [--celery|--no-celery]" >&2
      exit 1
      ;;
  esac
done

# Start Celery components to handle queued email if enabled
if [ "$CELERY" = true ]; then
  celery -A config worker -l info &
  CELERY_WORKER_PID=$!
  celery -A config beat -l info &
  CELERY_BEAT_PID=$!
  trap 'kill "$CELERY_WORKER_PID" "$CELERY_BEAT_PID"' EXIT
fi

# Start the Django development server
if [ "$RELOAD" = true ]; then
  python manage.py runserver 0.0.0.0:"$PORT"
else
  python manage.py runserver 0.0.0.0:"$PORT" --noreload
fi
