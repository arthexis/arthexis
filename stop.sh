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
LCD_LOCK="$LOCK_DIR/lcd_screen.lck"
CHARGING_LOCK="$LOCK_DIR/charging.lck"
PYTHON="python3"
if [ -d "$BASE_DIR/.venv" ]; then
  PYTHON="$BASE_DIR/.venv/bin/python"
fi

# Use non-interactive sudo if available
SUDO="sudo -n"
if ! $SUDO true 2>/dev/null; then
  SUDO=""
fi

FORCE=false
ALL=false
DEFAULT_PORT="$(arthexis_detect_backend_port "$BASE_DIR")"
PORT="$DEFAULT_PORT"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)
      ALL=true
      shift
      ;;
    --force)
      FORCE=true
      shift
      ;;
    *)
      PORT="$1"
      shift
      ;;
  esac
done

if [ "$FORCE" != true ]; then
  ACTIVE_OUTPUT=$(
    "$PYTHON" "$BASE_DIR/manage.py" shell <<'PY' 2>&1
import os
from django.conf import settings
from django.db import connections

override = os.environ.get("ARTHEXIS_STOP_DB_PATH")
if override:
    settings.DATABASES["default"]["NAME"] = override
    connections.databases["default"]["NAME"] = override

from ocpp.models import Transaction

active_sessions = Transaction.objects.filter(stop_time__isnull=True)
active_sessions = active_sessions.filter(
    charger__isnull=False,
    charger__is_deleted=False,
    charger__connector_id__isnull=False,
)

print(active_sessions.count())
PY
  )
  ACTIVE_STATUS=$?
  if [ "$ACTIVE_STATUS" -ne 0 ]; then
    printf '%s\n' "$ACTIVE_OUTPUT" >&2
    echo "Unable to verify active charging sessions. Resolve the issue or re-run with --force during a maintenance window." >&2
    exit 1
  fi
  ACTIVE_SESSIONS=$(printf '%s' "$ACTIVE_OUTPUT" | tail -n 1 | tr -d '\r\n ')
  if [[ ! "$ACTIVE_SESSIONS" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$ACTIVE_OUTPUT" >&2
    echo "Unexpected response while checking for active charging sessions. Resolve the issue or re-run with --force during a maintenance window." >&2
    exit 1
  fi
  if [ "$ACTIVE_SESSIONS" -gt 0 ]; then
    if [ -f "$CHARGING_LOCK" ]; then
      echo "Active charging sessions detected; aborting stop. Resolve the sessions or pass --force during a maintenance window." >&2
      exit 1
    fi
    echo "Recorded $ACTIVE_SESSIONS active session(s) but no charging lock detected; assuming the sessions are stale and continuing shutdown." >&2
  fi
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
