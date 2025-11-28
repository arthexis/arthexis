#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/ports.sh
. "$BASE_DIR/scripts/helpers/ports.sh"
# shellcheck source=scripts/helpers/service_manager.sh
. "$BASE_DIR/scripts/helpers/service_manager.sh"
# shellcheck source=scripts/helpers/suite-uptime-lock.sh
. "$BASE_DIR/scripts/helpers/suite-uptime-lock.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"

LOCK_DIR="$BASE_DIR/locks"
SERVICE_MANAGEMENT_MODE="$(arthexis_detect_service_mode "$LOCK_DIR")"

# Use non-interactive sudo if available
SUDO="sudo -n"
if ! $SUDO true 2>/dev/null; then
  SUDO=""
fi

ALL=false
FORCE=false
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

CHARGING_LOCK="$LOCK_DIR/charging.lck"

if [ -n "${ARTHEXIS_STOP_DB_PATH:-}" ]; then
  DB_PATH="$ARTHEXIS_STOP_DB_PATH"
  LOCK_MAX_AGE=${CHARGING_LOCK_MAX_AGE_SECONDS:-300}
  STALE_AFTER=${CHARGING_SESSION_STALE_AFTER_SECONDS:-86400}

  SESSION_COUNTS=$(python3 - <<'PY'
import os
import sqlite3
import time
from datetime import datetime

db_path = os.environ.get("ARTHEXIS_STOP_DB_PATH")
stale_after = int(os.environ.get("CHARGING_SESSION_STALE_AFTER_SECONDS", "86400"))
now = time.time()
active = 0
stale = 0

def to_epoch(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except Exception:
        return None

if db_path and os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT start_time, received_start_time, stop_time, connector_id FROM ocpp_transaction"
    )
    for start_time, received_start_time, stop_time, connector_id in cur.fetchall():
        if connector_id is None or stop_time is not None:
            continue
        active += 1
        ts = to_epoch(received_start_time) or to_epoch(start_time)
        if ts is not None and now - ts > stale_after:
            stale += 1
    conn.close()

print(f"{active} {stale}")
PY
  )
  read -r ACTIVE_COUNT STALE_COUNT <<<"$SESSION_COUNTS"

  if [ "${ACTIVE_COUNT:-0}" -gt 0 ]; then
    if [ -f "$CHARGING_LOCK" ]; then
      LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$CHARGING_LOCK") ))
      if [ "$LOCK_MAX_AGE" -ge 0 ] && [ "$LOCK_AGE" -gt "$LOCK_MAX_AGE" ]; then
        echo "Charging lock appears stale; continuing shutdown."
      elif [ "${STALE_COUNT:-0}" -gt 0 ]; then
        echo "Found ${STALE_COUNT} session(s) without recent activity; removing charging lock."
        rm -f "$CHARGING_LOCK"
      elif [ "$FORCE" = true ]; then
        echo "Active charging sessions detected but --force supplied; continuing shutdown."
      else
        echo "Active charging sessions detected; aborting stop." >&2
        exit 1
      fi
    else
      echo "Active charging sessions detected but no charging lock present; assuming the sessions are stale."
    fi
  fi
fi

# Allow callers (such as upgrades) to keep the LCD running a bit longer to
# display status by skipping the LCD stop step.
SKIP_LCD_STOP="${ARTHEXIS_SKIP_LCD_STOP:-0}"

arthexis_clear_suite_uptime_lock "$BASE_DIR" || true

# Stop systemd-managed services when present
if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE_NAME="$(cat "$LOCK_DIR/service.lck")"
  if systemctl list-unit-files | grep -Fq "${SERVICE_NAME}.service"; then
    $SUDO systemctl stop "$SERVICE_NAME" || true
    $SUDO systemctl status "$SERVICE_NAME" --no-pager || true

    CELERY_SERVICE="celery-$SERVICE_NAME"
    CELERY_BEAT_SERVICE="celery-beat-$SERVICE_NAME"
    CELERY_UNITS_FOUND=false
    if systemctl list-unit-files | awk '{print $1}' | grep -Fxq "${CELERY_BEAT_SERVICE}.service"; then
      CELERY_UNITS_FOUND=true
      $SUDO systemctl stop "$CELERY_BEAT_SERVICE" || true
      $SUDO systemctl status "$CELERY_BEAT_SERVICE" --no-pager || true
    fi
    if systemctl list-unit-files | awk '{print $1}' | grep -Fxq "${CELERY_SERVICE}.service"; then
      CELERY_UNITS_FOUND=true
      $SUDO systemctl stop "$CELERY_SERVICE" || true
      $SUDO systemctl status "$CELERY_SERVICE" --no-pager || true
    fi

    if [ "$CELERY_UNITS_FOUND" = false ]; then
      # Fall back to pkill when Celery services exist but aren't managed via systemd.
      pkill -f "celery -A config" || true
    fi

    if [ "$SKIP_LCD_STOP" != "1" ] && [ "$SKIP_LCD_STOP" != "true" ]; then
      LCD_SERVICE="lcd-$SERVICE_NAME"
      if arthexis_lcd_feature_enabled "$LOCK_DIR" || systemctl list-unit-files | awk '{print $1}' | grep -Fxq "${LCD_SERVICE}.service"; then
        $SUDO systemctl stop "$LCD_SERVICE" || true
        $SUDO systemctl status "$LCD_SERVICE" --no-pager || true
      fi
    fi

    exit 0
  fi
fi

# Fall back to stopping locally-run processes
PATTERN="manage.py runserver"
if [ "$ALL" = true ]; then
  pkill -f "$PATTERN" || true
else
  pkill -f "$PATTERN 0.0.0.0:$PORT" || true
fi
# Also stop any Celery components started by start.sh
pkill -f "celery -A config" || true
if [ "$SKIP_LCD_STOP" != "1" ] && [ "$SKIP_LCD_STOP" != "true" ]; then
  if arthexis_lcd_feature_enabled "$LOCK_DIR"; then
    if [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_EMBEDDED" ] || ! command -v systemctl >/dev/null 2>&1; then
      pkill -f "python -m core\.lcd_screen" || true
    fi
  fi
fi
