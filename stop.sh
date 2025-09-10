#!/usr/bin/env bash
set -e

usage() {
  echo "Usage: $0 [--all] [PORT]"
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"
LOCK_DIR="$BASE_DIR/locks"
LCD_LOCK="$LOCK_DIR/lcd_screen.lck"
PYTHON="python3"
if [ -d "$BASE_DIR/.venv" ]; then
  PYTHON="$BASE_DIR/.venv/bin/python"
fi

# Use non-interactive sudo if available
SUDO="sudo -n"
if ! $SUDO true 2>/dev/null; then
  SUDO=""
fi

# If a systemd service was installed, stop it instead of killing processes
if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE_NAME="$(cat "$LOCK_DIR/service.lck")"
  if systemctl list-unit-files | grep -Fq "${SERVICE_NAME}.service"; then
    if $SUDO systemctl stop "$SERVICE_NAME" 2>/dev/null; then
      $SUDO systemctl status "$SERVICE_NAME" --no-pager || true
      if [ -f "$LOCK_DIR/celery.lck" ]; then
        CELERY_SERVICE="celery-$SERVICE_NAME"
        CELERY_BEAT_SERVICE="celery-beat-$SERVICE_NAME"
        if systemctl list-unit-files | grep -Fq "${CELERY_BEAT_SERVICE}.service"; then
          $SUDO systemctl stop "$CELERY_BEAT_SERVICE" || true
          $SUDO systemctl status "$CELERY_BEAT_SERVICE" --no-pager || true
        fi
        if systemctl list-unit-files | grep -Fq "${CELERY_SERVICE}.service"; then
          $SUDO systemctl stop "$CELERY_SERVICE" || true
          $SUDO systemctl status "$CELERY_SERVICE" --no-pager || true
        fi
      fi
      if [ -f "$LCD_LOCK" ]; then
        LCD_SERVICE="lcd-$SERVICE_NAME"
        "$PYTHON" - <<'PY'
from core.notifications import notify
notify("Goodbye!")
PY
        sleep 1
        if systemctl list-unit-files | grep -Fq "${LCD_SERVICE}.service"; then
          $SUDO systemctl stop "$LCD_SERVICE" || true
          $SUDO systemctl status "$LCD_SERVICE" --no-pager || true
        fi
      fi
      exit 0
    fi
  fi
fi

# Activate virtual environment if present
if [ -d .venv ]; then
  source .venv/bin/activate
fi

ALL=false
PORT=8888

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)
      ALL=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      PORT="$1"
      shift
      ;;
  esac
done

PATTERN="manage.py runserver"
if [ "$ALL" = true ]; then
  pkill -f "$PATTERN" || true
else
  pkill -f "$PATTERN 0.0.0.0:$PORT" || true
fi
# Also stop any Celery components started by start.sh
pkill -f "celery -A config" || true

# Wait for processes to fully terminate
if [ "$ALL" = true ]; then
  while pgrep -f "$PATTERN" >/dev/null 2>&1; do
    sleep 0.5
  done
else
  while pgrep -f "$PATTERN 0.0.0.0:$PORT" >/dev/null 2>&1; do
    sleep 0.5
  done
fi
while pgrep -f "celery -A config" >/dev/null 2>&1; do
  sleep 0.5
done


if [ -f "$LCD_LOCK" ]; then
  "$PYTHON" - <<'PY'
from core.notifications import notify
notify("Goodbye!")
PY
fi
