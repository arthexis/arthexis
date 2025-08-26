#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"
LOCK_DIR="$BASE_DIR/locks"

# If a systemd service was installed, restart it instead of launching a new process
if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE_NAME="$(cat "$LOCK_DIR/service.lck")"
  if systemctl list-unit-files | grep -Fq "${SERVICE_NAME}.service"; then
    sudo systemctl restart "$SERVICE_NAME"
    # Show status information so the user can verify the service state
    sudo systemctl status "$SERVICE_NAME" --no-pager
    exit 0
  fi
fi

# Ensure virtual environment is available
if [ ! -d .venv ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi
source .venv/bin/activate

# Determine default port based on nginx mode if present
PORT=""
RELOAD=false
CELERY=false
if [ -f "$LOCK_DIR/celery.lck" ]; then
  CELERY=true
fi
if [ -f "$LOCK_DIR/nginx_mode.lck" ]; then
  MODE="$(cat "$LOCK_DIR/nginx_mode.lck")"
else
  MODE="internal"
fi
if [ "$MODE" = "public" ]; then
  PORT=8000
else
  PORT=8888
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
      shift
      ;;
    --no-celery)
      CELERY=false
      shift
      ;;
    --public)
      PORT=8000
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

# Start required Celery components if requested
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
