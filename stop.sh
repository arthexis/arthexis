#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/env.sh
. "$BASE_DIR/scripts/helpers/env.sh"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/ports.sh
. "$BASE_DIR/scripts/helpers/ports.sh"
# shellcheck source=scripts/helpers/service_manager.sh
. "$BASE_DIR/scripts/helpers/service_manager.sh"
# shellcheck source=scripts/helpers/suite-uptime-lock.sh
. "$BASE_DIR/scripts/helpers/suite-uptime-lock.sh"
arthexis_load_env_file "$BASE_DIR"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"

LOCK_DIR="$BASE_DIR/.locks"
SERVICE_MANAGEMENT_MODE="$(arthexis_detect_service_mode "$LOCK_DIR")"
DJANGO_PID_FILE="$LOCK_DIR/django.pid"
CELERY_WORKER_PID_FILE="$LOCK_DIR/celery_worker.pid"
CELERY_BEAT_PID_FILE="$LOCK_DIR/celery_beat.pid"
LCD_PID_FILE="$LOCK_DIR/lcd.pid"

kill_from_pid_file() {
  local pid_file="$1"
  local name="$2"

  if [ ! -f "$pid_file" ]; then
    return 0
  fi

  local pid
  pid=$(tr -d '\r\n' < "$pid_file")

  if [ -z "$pid" ]; then
    rm -f "$pid_file"
    return 0
  fi

  if kill -0 "$pid" 2>/dev/null; then
    if [ -n "$name" ]; then
      echo "Stopping $name process (PID $pid) from $pid_file"
    fi
    kill "$pid" 2>/dev/null || true
  fi

  rm -f "$pid_file"
}

# Use non-interactive sudo if available
SUDO="sudo -n"
if ! $SUDO true 2>/dev/null; then
  SUDO=""
fi

ALL=false
FORCE=false
CONFIRM=false
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
      --confirm)
        CONFIRM=true
        shift
        ;;
      *)
        PORT="$1"
        shift
        ;;
    esac
  done

CHARGING_LOCK="$LOCK_DIR/charging.lck"
LOCK_MAX_AGE=${CHARGING_LOCK_MAX_AGE_SECONDS:-300}
STALE_AFTER=${CHARGING_SESSION_STALE_AFTER_SECONDS:-86400}
LISTEN_SECONDS=${CHARGING_SESSION_LISTEN_SECONDS:-5}
LOCK_RECENT_WINDOW=${CHARGING_LOCK_ACTIVE_WINDOW_SECONDS:-120}

DB_PATH="${ARTHEXIS_STOP_DB_PATH:-${ARTHEXIS_SQLITE_PATH:-$BASE_DIR/db.sqlite3}}"
export ARTHEXIS_STOP_DB_PATH="$DB_PATH"
export ARTHEXIS_STOP_BASE_DIR="$BASE_DIR"

SESSION_COUNTS=$(python3 - <<'PY'
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

db_path = os.environ.get("ARTHEXIS_STOP_DB_PATH")
base_dir = os.environ.get("ARTHEXIS_STOP_BASE_DIR", "")
stale_after = int(os.environ.get("CHARGING_SESSION_STALE_AFTER_SECONDS", "86400"))
heartbeat_window = int(
    os.environ.get("CHARGING_SESSION_HEARTBEAT_ACTIVE_WINDOW_SECONDS", "300")
)
now = time.time()
active = 0
stale = 0
simulator_running = False
simulator_ids = set()

def to_epoch(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except Exception:
        return None

def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        is not None
    )

def column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}

def load_simulator_state() -> bool:
    if not base_dir:
        return False
    state_file = Path(base_dir) / "apps" / "ocpp" / "simulator.json"
    if not state_file.exists():
        return False
    try:
        payload = json.loads(state_file.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    for entry in payload.values():
        if isinstance(entry, dict) and entry.get("running"):
            return True
    return False

def is_recent_heartbeat(value: str | None) -> bool:
    if heartbeat_window < 0:
        return True
    ts = to_epoch(value)
    if ts is None:
        return False
    return now - ts <= heartbeat_window

if db_path and os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    if table_exists(conn, "ocpp_simulator"):
        sim_columns = column_names(conn, "ocpp_simulator")
        select_fields = [field for field in ("cp_path", "serial_number") if field in sim_columns]
        if select_fields:
            query = "SELECT " + ", ".join(select_fields) + " FROM ocpp_simulator"
            if "is_deleted" in sim_columns:
                query += " WHERE is_deleted = 0"
            for row in conn.execute(query):
                for val in row:
                    if val:
                        simulator_ids.add(str(val))
    simulator_running = load_simulator_state()
    if table_exists(conn, "ocpp_transaction"):
        charger_table = "ocpp_charger"
        charger_columns = (
            column_names(conn, charger_table) if table_exists(conn, charger_table) else set()
        )
        charger_id_col = "charger_id" if "charger_id" in charger_columns else None
        heartbeat_col = "last_heartbeat" if "last_heartbeat" in charger_columns else None
        join_clause = ""
        if charger_id_col:
            join_clause = f"LEFT JOIN {charger_table} ON ocpp_transaction.charger_id = {charger_table}.id"
            select_charger = f", {charger_table}.{charger_id_col}"
        else:
            select_charger = ", NULL"
        if heartbeat_col:
            select_heartbeat = f", {charger_table}.{heartbeat_col}"
        else:
            select_heartbeat = ", NULL"
        query = (
            "SELECT ocpp_transaction.start_time, "
            "ocpp_transaction.received_start_time, "
            "ocpp_transaction.stop_time, "
            "ocpp_transaction.connector_id"
            f"{select_charger} "
            f"{select_heartbeat} "
            "FROM ocpp_transaction "
            f"{join_clause} "
            "WHERE ocpp_transaction.stop_time IS NULL AND ocpp_transaction.connector_id IS NOT NULL"
        )
        for (
            start_time,
            received_start_time,
            stop_time,
            connector_id,
            charger_id,
            charger_heartbeat,
        ) in conn.execute(query).fetchall():
            if heartbeat_col and not is_recent_heartbeat(charger_heartbeat):
                continue
            if (
                charger_id
                and simulator_ids
                and not simulator_running
                and str(charger_id) in simulator_ids
            ):
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
  ACTIVE_SESSIONS_DETECTED=true
  if [ "${STALE_COUNT:-0}" -ge "${ACTIVE_COUNT:-0}" ]; then
    ACTIVE_SESSIONS_DETECTED=false
    if [ -f "$CHARGING_LOCK" ]; then
      LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$CHARGING_LOCK") ))
      if [ "$LOCK_MAX_AGE" -ge 0 ] && [ "$LOCK_AGE" -gt "$LOCK_MAX_AGE" ]; then
        echo "Charging lock appears stale; continuing shutdown."
      else
        LOCK_ACTIVITY=$(python3 - "$CHARGING_LOCK" "$LISTEN_SECONDS" "$LOCK_RECENT_WINDOW" <<'PY'
import os
import sys
import time

lock_path = sys.argv[1]
listen_seconds = int(sys.argv[2])
recent_window = int(sys.argv[3])

try:
    initial_mtime = os.path.getmtime(lock_path)
except FileNotFoundError:
    print("inactive")
    sys.exit(0)

now = time.time()
if now - initial_mtime <= recent_window:
    print("active")
    sys.exit(0)

deadline = now + listen_seconds
        while time.time() < deadline:
            time.sleep(1)
            try:
                if os.path.getmtime(lock_path) != initial_mtime:
                    print("active")
                    sys.exit(0)
            except FileNotFoundError:
                print("inactive")
                sys.exit(0)

print("inactive")
PY
        )
        if [ "$LOCK_ACTIVITY" = "active" ]; then
          ACTIVE_SESSIONS_DETECTED=true
          echo "Charging lock updated recently; treating stale sessions as active."
        else
          echo "No recent charging activity detected; removing charging lock."
          rm -f "$CHARGING_LOCK"
        fi
      fi
    else
      echo "Active charging sessions detected but no charging lock present; assuming the sessions are stale."
    fi
  fi

  if [ "$ACTIVE_SESSIONS_DETECTED" = true ]; then
    if [ "$FORCE" = true ]; then
      if [ "$CONFIRM" = true ]; then
        echo "Active charging sessions detected but --force and --confirm supplied; continuing shutdown."
      elif [ -t 0 ]; then
        read -r -p "Active charging sessions detected. Proceed with shutdown? [y/N] " response
        case "$response" in
          [yY]|[yY][eE][sS])
            echo "Shutdown confirmed; continuing despite active charging sessions."
            ;;
          *)
            echo "Shutdown aborted by user." >&2
            exit 1
            ;;
        esac
      else
        echo "Active charging sessions detected; rerun with --confirm to override in non-interactive shells." >&2
        exit 1
      fi
    else
      echo "Active charging sessions detected; aborting stop." >&2
      exit 1
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
      arthexis_stop_embedded_lcd_processes "$LOCK_DIR"
    fi

    RFID_SERVICE="rfid-$SERVICE_NAME"
    if [ -f "$LOCK_DIR/$ARTHEXIS_RFID_SERVICE_LOCK" ] || systemctl list-unit-files | awk '{print $1}' | grep -Fxq "${RFID_SERVICE}.service"; then
      $SUDO systemctl stop "$RFID_SERVICE" || true
      $SUDO systemctl status "$RFID_SERVICE" --no-pager || true
    fi

    CAMERA_SERVICE="camera-$SERVICE_NAME"
    if [ -f "$LOCK_DIR/$ARTHEXIS_CAMERA_SERVICE_LOCK" ] || systemctl list-unit-files | awk '{print $1}' | grep -Fxq "${CAMERA_SERVICE}.service"; then
      $SUDO systemctl stop "$CAMERA_SERVICE" || true
      $SUDO systemctl status "$CAMERA_SERVICE" --no-pager || true
    fi

    exit 0
  fi
fi

# Stop locally tracked processes when not using systemd
kill_from_pid_file "$DJANGO_PID_FILE" "Django server"
kill_from_pid_file "$CELERY_WORKER_PID_FILE" "Celery worker"
kill_from_pid_file "$CELERY_BEAT_PID_FILE" "Celery beat"
if [ "$SKIP_LCD_STOP" != "1" ] && [ "$SKIP_LCD_STOP" != "true" ]; then
  kill_from_pid_file "$LCD_PID_FILE" "LCD screen"
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
if [ -f "$LOCK_DIR/$ARTHEXIS_RFID_SERVICE_LOCK" ]; then
  pkill -f "manage.py rfid_service" || true
fi
if [ -f "$LOCK_DIR/$ARTHEXIS_CAMERA_SERVICE_LOCK" ]; then
  pkill -f "manage.py camera_service" || true
fi
if [ "$SKIP_LCD_STOP" != "1" ] && [ "$SKIP_LCD_STOP" != "true" ]; then
  if arthexis_lcd_feature_enabled "$LOCK_DIR"; then
    if [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_EMBEDDED" ] || ! command -v systemctl >/dev/null 2>&1; then
      arthexis_stop_embedded_lcd_processes "$LOCK_DIR"
    fi
  fi
fi
