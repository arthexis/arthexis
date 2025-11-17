#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/ports.sh
. "$BASE_DIR/scripts/helpers/ports.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"
LOCK_DIR="$BASE_DIR/locks"
SKIP_LOCK="$LOCK_DIR/service-start-skip.lck"
SYSTEMD_LOCK_FILE="$LOCK_DIR/systemd_services.lck"
SERVICE_NAME=""
if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE_NAME=$(tr -d '\r\n' < "$LOCK_DIR/service.lck")
fi

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

# Determine whether to skip auto-upgrade once
SKIP_UPGRADE=false
if [ -f "$SKIP_LOCK" ]; then
  now=$(date +%s)
  modified=""
  if stat -c %Y "$SKIP_LOCK" >/dev/null 2>&1; then
    modified=$(stat -c %Y "$SKIP_LOCK")
  elif stat -f %m "$SKIP_LOCK" >/dev/null 2>&1; then
    modified=$(stat -f %m "$SKIP_LOCK")
  fi
  if [ -n "$modified" ] && [ $((now - modified)) -le 300 ]; then
    SKIP_UPGRADE=true
  else
    echo "Ignoring stale manual start lock older than 5 minutes."
  fi
  rm -f "$SKIP_LOCK"
fi

# Run auto-upgrade during startup unless a one-time skip was requested
if [ "$SKIP_UPGRADE" != true ] && [ -f "$LOCK_DIR/auto_upgrade.lck" ]; then
  MODE=$(tr -d '\r\n' < "$LOCK_DIR/auto_upgrade.lck" | tr 'A-Z' 'a-z')
  [ -n "$MODE" ] || MODE="version"
  UPGRADE_ARGS=("$BASE_DIR/upgrade.sh" "--no-restart")
  case "$MODE" in
    latest)
      UPGRADE_ARGS+=("--latest")
      ;;
    stable)
      UPGRADE_ARGS+=("--stable")
      ;;
    *)
      ;;
  esac
  echo "Running startup upgrade with mode '$MODE'..."
  "${UPGRADE_ARGS[@]}"
fi

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

# Determine default port based on nginx mode if present
DEFAULT_PORT="$(arthexis_detect_backend_port "$BASE_DIR")"
PORT="$DEFAULT_PORT"
RELOAD=false
# Celery workers process Post Office's email queue; enable by default unless
# systemd-managed Celery units are present. Those are installed by install.sh
# when the service name is provided.
CELERY=true
CELERY_FLAG_SET=false
SYSTEMD_CELERY_UNITS=false
if [ -n "$SERVICE_NAME" ] && [ -f "$SYSTEMD_LOCK_FILE" ]; then
  if grep -Fxq "celery-${SERVICE_NAME}.service" "$SYSTEMD_LOCK_FILE" || \
     grep -Fxq "celery-beat-${SERVICE_NAME}.service" "$SYSTEMD_LOCK_FILE"; then
    SYSTEMD_CELERY_UNITS=true
  fi
fi
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
      CELERY_FLAG_SET=true
      shift
      ;;
    --no-celery)
      CELERY=false
      CELERY_FLAG_SET=true
      shift
      ;;
    --public)
      PORT="$DEFAULT_PORT"
      shift
      ;;
    --internal)
      PORT="$DEFAULT_PORT"
      shift
      ;;
    *)
      echo "Usage: $0 [--port PORT] [--reload] [--public|--internal] [--celery|--no-celery]" >&2
      exit 1
      ;;
  esac
done

if [ "$CELERY_FLAG_SET" = false ] && [ "$SYSTEMD_CELERY_UNITS" = true ]; then
  echo "Skipping embedded Celery processes because systemd-managed units are enabled. Use --celery to override."
  CELERY=false
fi

# Start Celery components to handle queued email if enabled
if [ "$CELERY" = true ]; then
  celery -A config worker -l info --concurrency=2 &
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
