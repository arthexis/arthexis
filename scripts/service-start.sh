#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export TZ="${TZ:-America/Monterrey}"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/ports.sh
. "$BASE_DIR/scripts/helpers/ports.sh"
# shellcheck source=scripts/helpers/service_manager.sh
. "$BASE_DIR/scripts/helpers/service_manager.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
ERROR_LOG="$LOG_DIR/error.log"
: > "$ERROR_LOG"
exec > >(tee "$LOG_FILE") 2> >(tee -a "$ERROR_LOG" >&2)
cd "$BASE_DIR"
LOG_FOLLOW_PID=""

normalize_log_level() {
  local raw_level="$1"
  if [ -z "$raw_level" ]; then
    return 1
  fi

  local normalized
  normalized=$(echo "$raw_level" | tr '[:lower:]' '[:upper:]')
  case "$normalized" in
    DEBUG|INFO|WARNING|ERROR|CRITICAL)
      echo "$normalized"
      return 0
      ;;
    WARN)
      echo "WARNING"
      return 0
      ;;
    FATAL)
      echo "CRITICAL"
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

log_level_priority() {
  case "$1" in
    DEBUG)
      echo 0
      ;;
    INFO)
      echo 1
      ;;
    WARNING)
      echo 2
      ;;
    ERROR)
      echo 3
      ;;
    CRITICAL)
      echo 4
      ;;
    *)
      return 1
      ;;
  esac
}

extract_line_level() {
  local line="$1"
  if [[ "$line" =~ \[([A-Z]+)\] ]]; then
    echo "${BASH_REMATCH[1]}"
  fi
}

start_log_follower() {
  local log_file="$1"
  local minimum_level="$2"

  if [ -z "$minimum_level" ]; then
    return 0
  fi

  local min_priority
  min_priority=$(log_level_priority "$minimum_level") || return 1
  touch "$log_file"
  echo "Streaming log entries from $log_file at level $minimum_level or higher..."

  tail -n0 -F "$log_file" | while IFS= read -r line; do
    local line_level
    line_level=$(extract_line_level "$line")
    if [ -z "$line_level" ]; then
      echo "$line"
      continue
    fi

    local priority
    priority=$(log_level_priority "$line_level") || priority=""
    if [ -z "$priority" ] || [ "$priority" -ge "$min_priority" ]; then
      echo "$line"
    fi
  done &

  LOG_FOLLOW_PID=$!
}
LOCK_DIR="$BASE_DIR/.locks"
STARTUP_LOCK="$LOCK_DIR/startup_started_at.lck"
SYSTEMD_LOCK_FILE="$LOCK_DIR/systemd_services.lck"
SERVICE_MANAGEMENT_MODE="$(arthexis_detect_service_mode "$LOCK_DIR")"
SERVICE_NAME=""
if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE_NAME=$(tr -d '\r\n' < "$LOCK_DIR/service.lck")
fi

mkdir -p "$LOCK_DIR"

DJANGO_PID_FILE="$LOCK_DIR/django.pid"
CELERY_WORKER_PID_FILE="$LOCK_DIR/celery_worker.pid"
CELERY_BEAT_PID_FILE="$LOCK_DIR/celery_beat.pid"
LCD_PID_FILE="$LOCK_DIR/lcd.pid"

record_pid_file() {
  local pid="$1"
  local file="$2"
  if [ -n "$pid" ] && [ -n "$file" ]; then
    printf '%s\n' "$pid" > "$file"
  fi
}

clear_pid_files() {
  rm -f "$DJANGO_PID_FILE" "$CELERY_WORKER_PID_FILE" "$CELERY_BEAT_PID_FILE" "$LCD_PID_FILE"
}

clear_pid_files

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

SOFT_FD_LIMIT="$(ulimit -Sn 2>/dev/null || echo "unknown")"
HARD_FD_LIMIT="$(ulimit -Hn 2>/dev/null || echo "unknown")"
echo "Open file limits: soft=${SOFT_FD_LIMIT} hard=${HARD_FD_LIMIT}"

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
# Whether to wait for the suite to become reachable after launching
AWAIT_START=false
STARTUP_TIMEOUT=300
DEBUG_MODE=false
SHOW_LEVEL=""
APP_LOG_FILE="$LOG_DIR/$(hostname).log"
# Celery workers process Post Office's email queue; prefer embedded mode.
CELERY_MANAGEMENT_MODE="$SERVICE_MANAGEMENT_MODE"
CELERY_FLAG_SET=false
SYSTEMD_CELERY_UNITS=false
LCD_FEATURE=false
LCD_SYSTEMD_UNIT=false
LCD_EMBEDDED=false
CELERY_WORKER_PID=""
CELERY_BEAT_PID=""
LCD_PROCESS_PID=""
DJANGO_SERVER_PID=""
cleanup_background_processes() {
  if [ -n "$CELERY_WORKER_PID" ]; then
    kill "$CELERY_WORKER_PID" 2>/dev/null || true
  fi
  if [ -n "$CELERY_BEAT_PID" ]; then
    kill "$CELERY_BEAT_PID" 2>/dev/null || true
  fi
  if [ -n "$LCD_PROCESS_PID" ]; then
    kill "$LCD_PROCESS_PID" 2>/dev/null || true
  fi
  if [ -n "$DJANGO_SERVER_PID" ]; then
    kill "$DJANGO_SERVER_PID" 2>/dev/null || true
  fi
  if [ -n "$LOG_FOLLOW_PID" ]; then
    kill "$LOG_FOLLOW_PID" 2>/dev/null || true
  fi
  clear_pid_files
}
trap cleanup_background_processes EXIT
if [ -n "$SERVICE_NAME" ] && [ -f "$SYSTEMD_LOCK_FILE" ]; then
  if grep -Fxq "celery-${SERVICE_NAME}.service" "$SYSTEMD_LOCK_FILE" || \
     grep -Fxq "celery-beat-${SERVICE_NAME}.service" "$SYSTEMD_LOCK_FILE"; then
    SYSTEMD_CELERY_UNITS=true
  fi
  if grep -Fxq "lcd-${SERVICE_NAME}.service" "$SYSTEMD_LOCK_FILE"; then
    LCD_SYSTEMD_UNIT=true
  fi
fi
if arthexis_lcd_feature_enabled "$LOCK_DIR"; then
  LCD_FEATURE=true
fi

queue_startup_net_message() {
  python - "$BASE_DIR" "$PORT" <<'PY'
import sys
from pathlib import Path

from apps.screens.startup_notifications import queue_startup_message

base_dir = Path(sys.argv[1])
port_value = sys.argv[2]

queue_startup_message(base_dir=base_dir, port=port_value)
PY
}
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
    --await)
      AWAIT_START=true
      shift
      ;;
    --debug)
      DEBUG_MODE=true
      shift
      ;;
    --show)
      if [ -z "${2:-}" ]; then
        echo "Usage: $0 [--port PORT] [--reload] [--await] [--debug] [--show LEVEL] [--public|--internal] [--embedded|--systemd|--no-celery]" >&2
        exit 1
      fi
      SHOW_LEVEL="$2"
      shift 2
      ;;
    --embedded|--celery)
      CELERY_MANAGEMENT_MODE="$ARTHEXIS_SERVICE_MODE_EMBEDDED"
      CELERY_FLAG_SET=true
      shift
      ;;
    --systemd)
      CELERY_MANAGEMENT_MODE="$ARTHEXIS_SERVICE_MODE_SYSTEMD"
      CELERY_FLAG_SET=true
      shift
      ;;
    --no-celery)
      CELERY_MANAGEMENT_MODE="disabled"
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
      echo "Usage: $0 [--port PORT] [--reload] [--await] [--debug] [--show LEVEL] [--public|--internal] [--embedded|--systemd|--no-celery]" >&2
      exit 1
      ;;
  esac
done

if [ -n "$SHOW_LEVEL" ]; then
  if ! SHOW_LEVEL=$(normalize_log_level "$SHOW_LEVEL"); then
    echo "Invalid log level: $SHOW_LEVEL" >&2
    exit 1
  fi
fi

if [ "$SHOW_LEVEL" = "DEBUG" ]; then
  DEBUG_MODE=true
fi

if [ "$DEBUG_MODE" = true ]; then
  export DEBUG=1
fi

start_log_follower "$APP_LOG_FILE" "$SHOW_LEVEL"

arthexis_suite_reachable() {
  local port="$1"
  if [ -z "$port" ]; then
    return 1
  fi

  local python_bin
  if command -v python3 >/dev/null 2>&1; then
    python_bin=python3
  elif command -v python >/dev/null 2>/dev/null; then
    python_bin=python
  else
    return 1
  fi

  "$python_bin" - "$port" <<'PY'
import socket
import sys

try:
    port_value = int(sys.argv[1])
except (IndexError, ValueError):
    sys.exit(1)

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(2)
    try:
        sock.connect(("127.0.0.1", port_value))
    except OSError:
        sys.exit(1)

sys.exit(0)
PY
}

wait_for_suite_startup() {
  local port="$1"
  local server_pid="$2"
  local timeout_seconds="$3"
  local start_time
  start_time=$(date +%s)

  echo "Waiting for suite to become reachable on port $port (timeout ${timeout_seconds}s)..."

  while true; do
    if [ -n "$server_pid" ] && ! kill -0 "$server_pid" 2>/dev/null; then
      echo "Web server process ($server_pid) exited before readiness was confirmed."
      if [ -s "$ERROR_LOG" ]; then
        echo "Recent errors from $ERROR_LOG:"
        tail -n 40 "$ERROR_LOG"
      elif [ -f "$ERROR_LOG" ]; then
        echo "No errors captured in $ERROR_LOG."
      fi
      return 1
    fi

    if arthexis_suite_reachable "$port"; then
      echo "Suite is reachable at http://localhost:$port"
      return 0
    fi

    if [ $(( $(date +%s) - start_time )) -ge "$timeout_seconds" ]; then
      echo "Timed out waiting for the suite to become reachable on port $port."
      if [ -s "$ERROR_LOG" ]; then
        echo "Recent errors from $ERROR_LOG:"
        tail -n 40 "$ERROR_LOG"
      elif [ -f "$ERROR_LOG" ]; then
        echo "No errors captured in $ERROR_LOG."
      fi
      return 1
    fi

    sleep 2
  done
}

STARTUP_STARTED_AT=$(date +%s)
{
  printf '%s\n' "$STARTUP_STARTED_AT"
  printf 'port=%s\n' "$PORT"
} > "$STARTUP_LOCK"

CELERY=true
case "$CELERY_MANAGEMENT_MODE" in
  "$ARTHEXIS_SERVICE_MODE_SYSTEMD")
    CELERY=false
    ;;
  disabled)
    CELERY=false
    ;;
  *)
    CELERY=true
    if [ "$CELERY_FLAG_SET" = false ] && [ "$SYSTEMD_CELERY_UNITS" = true ]; then
      echo "Skipping systemd-managed Celery units because embedded workers are enabled. Use --systemd to override."
    fi
    ;;
esac

if [ "$LCD_FEATURE" = true ]; then
  if [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ] && [ "$LCD_SYSTEMD_UNIT" = true ]; then
    LCD_EMBEDDED=false
  else
    LCD_EMBEDDED=true
    if [ "$LCD_SYSTEMD_UNIT" = true ]; then
      echo "Skipping systemd-managed LCD service because embedded mode is enabled. Reinstall with --systemd to manage the LCD via systemd."
    fi
  fi
fi

if [ "$LCD_FEATURE" = true ]; then
  if ! queue_startup_net_message; then
    echo "Failed to queue startup Net Message" >&2
  fi
fi

RUNSERVER_EXTRA_ARGS=()
run_runserver_preflight() {
  if [ "${RUNSERVER_PREFLIGHT_DONE:-false}" = true ]; then
    return 0
  fi

  echo "Running Django migration check once before runserver..."
  python manage.py migrate --check

  RUNSERVER_PREFLIGHT_DONE=true
  export DJANGO_SUPPRESS_MIGRATION_CHECK=1
  RUNSERVER_EXTRA_ARGS+=("--skip-checks")
}

# Start Celery components to handle queued email if enabled
if [ "$CELERY" = true ]; then
  celery -A config worker -l info --concurrency=2 &
  CELERY_WORKER_PID=$!
  record_pid_file "$CELERY_WORKER_PID" "$CELERY_WORKER_PID_FILE"
  celery -A config beat -l info &
  CELERY_BEAT_PID=$!
  record_pid_file "$CELERY_BEAT_PID" "$CELERY_BEAT_PID_FILE"
fi

if [ "$LCD_EMBEDDED" = true ]; then
  python -m apps.screens.lcd_screen &
  LCD_PROCESS_PID=$!
  record_pid_file "$LCD_PROCESS_PID" "$LCD_PID_FILE"
fi

# Start the Django development server
if ! run_runserver_preflight; then
  echo "Runserver preflight checks failed; aborting startup." >&2
  exit 1
fi

if [ "$AWAIT_START" = true ]; then
  if [ "$RELOAD" = true ]; then
    python manage.py runserver 0.0.0.0:"$PORT" "${RUNSERVER_EXTRA_ARGS[@]}" &
  else
    python manage.py runserver 0.0.0.0:"$PORT" --noreload "${RUNSERVER_EXTRA_ARGS[@]}" &
  fi
  DJANGO_SERVER_PID=$!
  record_pid_file "$DJANGO_SERVER_PID" "$DJANGO_PID_FILE"

  if wait_for_suite_startup "$PORT" "$DJANGO_SERVER_PID" "$STARTUP_TIMEOUT"; then
    wait "$DJANGO_SERVER_PID"
  else
    exit 1
  fi
else
  if [ "$RELOAD" = true ]; then
    python manage.py runserver 0.0.0.0:"$PORT" "${RUNSERVER_EXTRA_ARGS[@]}" &
  else
    python manage.py runserver 0.0.0.0:"$PORT" --noreload "${RUNSERVER_EXTRA_ARGS[@]}" &
  fi
  DJANGO_SERVER_PID=$!
  record_pid_file "$DJANGO_SERVER_PID" "$DJANGO_PID_FILE"
  wait "$DJANGO_SERVER_PID"
fi
