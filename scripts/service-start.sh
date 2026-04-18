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
# shellcheck source=scripts/helpers/staticfiles.sh
. "$BASE_DIR/scripts/helpers/staticfiles.sh"
# shellcheck source=scripts/helpers/suite-uptime-lock.sh
. "$BASE_DIR/scripts/helpers/suite-uptime-lock.sh"
# shellcheck source=scripts/helpers/debug_toolbar.sh
. "$BASE_DIR/scripts/helpers/debug_toolbar.sh"
# shellcheck source=scripts/helpers/timing.sh
. "$BASE_DIR/scripts/helpers/timing.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
ERROR_LOG="$LOG_DIR/error.log"
DEFAULT_LOG_DIR="$BASE_DIR/logs"
if [ "${LOG_DIR%/}" = "${DEFAULT_LOG_DIR%/}" ]; then
  arthexis_mark_log_breaks "$(basename "$0")" "$LOG_DIR"
else
  arthexis_mark_log_breaks "$(basename "$0")" "$LOG_DIR" "$DEFAULT_LOG_DIR"
fi
exec > >(tee -a "$LOG_FILE") 2> >(tee -a "$ERROR_LOG" >&2)
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
STARTUP_DURATION_LOCK="$LOCK_DIR/startup_duration.lck"
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

declare -Ag STARTUP_PHASE_STARTED_MS
declare -Ag STARTUP_PHASE_FINISHED_MS
declare -Ag STARTUP_PHASE_STATUS
declare -ag STARTUP_PHASE_ORDER

startup_phase_begin() {
  local phase_name="$1"
  if [ -z "$phase_name" ]; then
    return 0
  fi
  if [ -z "${STARTUP_PHASE_STARTED_MS[$phase_name]:-}" ]; then
    STARTUP_PHASE_ORDER+=("$phase_name")
  fi
  STARTUP_PHASE_STARTED_MS["$phase_name"]="$(arthexis_timing_now_ms)"
}

startup_phase_finish() {
  local phase_name="$1"
  local status="${2:-completed}"
  local started_ms="${STARTUP_PHASE_STARTED_MS[$phase_name]:-}"
  if [ -z "$phase_name" ] || [ -z "$started_ms" ]; then
    return 0
  fi
  STARTUP_PHASE_FINISHED_MS["$phase_name"]="$(arthexis_timing_now_ms)"
  STARTUP_PHASE_STATUS["$phase_name"]="$status"
}

startup_phase_timings_json() {
  {
    local phase_name
    for phase_name in "${STARTUP_PHASE_ORDER[@]}"; do
      local started_ms="${STARTUP_PHASE_STARTED_MS[$phase_name]:-}"
      local finished_ms="${STARTUP_PHASE_FINISHED_MS[$phase_name]:-}"
      local status="${STARTUP_PHASE_STATUS[$phase_name]:-completed}"
      if [ -z "$started_ms" ] || [ -z "$finished_ms" ]; then
        continue
      fi
      printf '%s\t%s\t%s\t%s\n' "$phase_name" "$started_ms" "$finished_ms" "$status"
    done
  } | python - <<'PY'
import json
import sys
from datetime import datetime, timezone

entries = []
for raw_line in sys.stdin:
    line = raw_line.rstrip("\n")
    if not line:
        continue
    try:
        name, started_ms, finished_ms, status = line.split("\t", 3)
    except ValueError:
        continue
    started_value = int(started_ms)
    finished_value = int(finished_ms)
    duration_ms = max(finished_value - started_value, 0)
    entries.append(
        {
            "name": name,
            "started_at": datetime.fromtimestamp(
                started_value / 1000, tz=timezone.utc
            ).isoformat(),
            "finished_at": datetime.fromtimestamp(
                finished_value / 1000, tz=timezone.utc
            ).isoformat(),
            "duration_ms": duration_ms,
            "status": status,
        }
    )

print(json.dumps(entries, sort_keys=False))
PY
}

wait_for_suite_startup_timed() {
  local port="$1"
  local server_pid="$2"
  local timeout_seconds="$3"
  startup_phase_begin "readiness_wait"
  if wait_for_suite_startup "$port" "$server_pid" "$timeout_seconds"; then
    startup_phase_finish "readiness_wait"
    return 0
  fi
  startup_phase_finish "readiness_wait" "error"
  return 1
}

# Ensure virtual environment is available
startup_phase_begin "runtime_bootstrap"
if [ ! -d .venv ]; then
  startup_phase_finish "runtime_bootstrap" "error"
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
startup_phase_finish "runtime_bootstrap"

SOFT_FD_LIMIT="$(ulimit -Sn 2>/dev/null || echo "unknown")"
HARD_FD_LIMIT="$(ulimit -Hn 2>/dev/null || echo "unknown")"
echo "Open file limits: soft=${SOFT_FD_LIMIT} hard=${HARD_FD_LIMIT}"

# Determine default port based on nginx mode if present
DEFAULT_PORT="$(arthexis_detect_backend_port "$BASE_DIR")"
PORT="$DEFAULT_PORT"
RELOAD=false
# Whether to wait for the suite to become reachable after launching
AWAIT_START=false
STARTUP_TIMEOUT=300
DEBUG_MODE=false
FORCE_COLLECTSTATIC=false
SHOW_LEVEL=""
FOLLOW_LOGS=false
APP_LOG_FILE="$LOG_DIR/$(hostname).log"
# Celery workers process Post Office's email queue; prefer embedded mode.
CELERY_MANAGEMENT_MODE="$SERVICE_MANAGEMENT_MODE"
CELERY_FLAG_SET=false
LCD_EMBEDDED=false
CELERY_EMBEDDED=false
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
        echo "Usage: $0 [--port PORT] [--reload] [--await] [--debug] [--show LEVEL] [--embedded|--systemd|--celery] [--force-collectstatic] [--log-follow]" >&2
        exit 1
      fi
      SHOW_LEVEL="$2"
      FOLLOW_LOGS=true
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
    --force-collectstatic)
      FORCE_COLLECTSTATIC=true
      shift
      ;;
    --log-follow)
      FOLLOW_LOGS=true
      shift
      ;;
    *)
      echo "Usage: $0 [--port PORT] [--reload] [--await] [--debug] [--show LEVEL] [--embedded|--systemd|--celery] [--force-collectstatic] [--log-follow]" >&2
      exit 1
      ;;
  esac
done

if [ "$FOLLOW_LOGS" = true ] && [ -z "$SHOW_LEVEL" ]; then
  SHOW_LEVEL="INFO"
fi

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
  arthexis_ensure_debug_toolbar_installed "python"
fi

if [ "$FOLLOW_LOGS" = true ]; then
  start_log_follower "$APP_LOG_FILE" "$SHOW_LEVEL"
fi

STATIC_MD5_FILE="$LOCK_DIR/staticfiles.md5"
STATIC_META_FILE="$LOCK_DIR/staticfiles.meta"
STATIC_HASH=""
STORED_HASH=""
[ -f "$STATIC_MD5_FILE" ] && STORED_HASH=$(cat "$STATIC_MD5_FILE")

startup_phase_begin "staticfiles"
if [ "$FORCE_COLLECTSTATIC" = false ]; then
  set +e
  STATIC_HASH=$(arthexis_staticfiles_snapshot_check "$STATIC_MD5_FILE" "$STATIC_META_FILE")
  FAST_PATH_STATUS=$?
  set -e

  if [ "$FAST_PATH_STATUS" -eq 0 ] && [ -n "$STATIC_HASH" ]; then
    echo "Static files unchanged since last run; using lock metadata."
  elif [ "$FAST_PATH_STATUS" -ne 3 ]; then
    echo "Static files metadata unavailable (exit $FAST_PATH_STATUS); recalculating."
    STATIC_HASH=""
  else
    STATIC_HASH=""
  fi
fi

if [ -z "$STATIC_HASH" ]; then
  if ! STATIC_HASH=$(arthexis_staticfiles_compute_hash "$STATIC_MD5_FILE" "$STATIC_META_FILE" "$FORCE_COLLECTSTATIC"); then
    echo "Failed to compute static files hash; running collectstatic."
    arthexis_staticfiles_clear_staged_lock
    python manage.py collectstatic --noinput
    STATIC_HASH=""
  fi
fi

if [ "$FORCE_COLLECTSTATIC" = true ] || [ -z "$STATIC_HASH" ] || [ "$STATIC_HASH" != "$STORED_HASH" ]; then
  if python manage.py collectstatic --noinput; then
    arthexis_staticfiles_commit_staged_lock "$STATIC_MD5_FILE" "$STATIC_META_FILE"
  else
    echo "collectstatic failed"
    arthexis_staticfiles_clear_staged_lock
    exit 1
  fi
else
  arthexis_staticfiles_clear_staged_lock
  echo "Static files unchanged. Skipping collectstatic."
fi
startup_phase_finish "staticfiles"

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

record_startup_duration() {
  local status="${1:-0}"
  local end_time
  end_time=$(date +%s)
  local duration=$((end_time - STARTUP_STARTED_AT))
  local phase_timings_json
  phase_timings_json="$(startup_phase_timings_json)"
  STARTUP_PHASE_TIMINGS_JSON="$phase_timings_json" python - "$STARTUP_DURATION_LOCK" "$STARTUP_STARTED_AT" "$end_time" "$duration" "$status" "$PORT" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

lock_path = Path(sys.argv[1])
started_at = int(sys.argv[2])
finished_at = int(sys.argv[3])
duration = int(sys.argv[4])
status = int(sys.argv[5])
port = sys.argv[6] if len(sys.argv) > 6 else ""
phase_timings = []
raw_phase_timings = os.environ.get("STARTUP_PHASE_TIMINGS_JSON", "")
if raw_phase_timings:
    try:
        parsed_phase_timings = json.loads(raw_phase_timings)
    except json.JSONDecodeError:
        parsed_phase_timings = []
    if isinstance(parsed_phase_timings, list):
        phase_timings = parsed_phase_timings

payload = {
    "started_at": datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat(),
    "finished_at": datetime.fromtimestamp(finished_at, tz=timezone.utc).isoformat(),
    "duration_seconds": duration,
    "status": status,
    "port": port,
    "phase_timings": phase_timings,
}

lock_path.parent.mkdir(parents=True, exist_ok=True)
lock_path.write_text(json.dumps(payload), encoding="utf-8")
PY
}

STARTUP_STARTED_AT=$(date +%s)
ORCHESTRATE_OUTPUT_FILE="$(mktemp)"
startup_phase_begin "startup_orchestrate"
if ! python manage.py startup_orchestrate \
  --port "$PORT" \
  --lock-dir "$LOCK_DIR" \
  --service-name "$SERVICE_NAME" \
  --service-mode "$SERVICE_MANAGEMENT_MODE" \
  --celery-mode "$CELERY_MANAGEMENT_MODE" > "$ORCHESTRATE_OUTPUT_FILE"; then
  startup_phase_finish "startup_orchestrate" "error"
  echo "Startup orchestration failed; aborting startup." >&2
  cat "$ORCHESTRATE_OUTPUT_FILE" >&2 || true
  rm -f "$ORCHESTRATE_OUTPUT_FILE"
  exit 1
fi
startup_phase_finish "startup_orchestrate"

readarray -t ORCHESTRATE_EXPORTS < <(
  python - "$ORCHESTRATE_OUTPUT_FILE" <<'PY'
import pathlib
import sys

from scripts.startup_orchestration import extract_payload

payload = extract_payload(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
launch = payload.get("launch") or {}
service = payload.get("service") or {}

def emit(name, value):
    print(f"{name}={value}")

emit("ORCHESTRATE_STATUS", payload.get("status") or "error")
emit("STARTUP_STARTED_AT", int(payload.get("started_at_epoch") or 0))
emit("CELERY_EMBEDDED", "true" if bool(launch.get("celery_embedded")) else "false")
emit("LCD_EMBEDDED", "true" if bool(launch.get("lcd_embedded")) else "false")
emit("LCD_TARGET_MODE", launch.get("lcd_target_mode") or "embedded")
emit("LCD_SYSTEMD_UNIT", "true" if bool(service.get("lcd_systemd_unit")) else "false")
PY
)
rm -f "$ORCHESTRATE_OUTPUT_FILE"
for export_line in "${ORCHESTRATE_EXPORTS[@]}"; do
  eval "$export_line"
done

if [ "${ORCHESTRATE_STATUS:-error}" != "ok" ]; then
  echo "Startup orchestration returned error status; aborting startup." >&2
  exit 1
fi

if [ "$STARTUP_STARTED_AT" -le 0 ]; then
  STARTUP_STARTED_AT=$(date +%s)
fi

if [ "$LCD_TARGET_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ] || [ "$LCD_EMBEDDED" = true ]; then
  startup_phase_begin "lcd_coordination"
  arthexis_disable_lcd_modes "$LOCK_DIR" "$SERVICE_NAME"
  startup_phase_finish "lcd_coordination"
fi

if [ "$LCD_TARGET_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ] && [ -n "$SERVICE_NAME" ]; then
  startup_phase_begin "lcd_target_activation"
  arthexis_start_systemd_unit_if_present "lcd-${SERVICE_NAME}.service"
  startup_phase_finish "lcd_target_activation"
elif [ "$LCD_EMBEDDED" = true ] && [ "$LCD_SYSTEMD_UNIT" = true ]; then
  echo "Skipping systemd-managed LCD service because embedded mode is enabled. Reinstall with --systemd to manage the LCD via systemd."
fi

RUNSERVER_EXTRA_ARGS=()

# Start Celery components to handle queued email if enabled
startup_phase_begin "celery_coordination"
if [ "$CELERY_EMBEDDED" = true ]; then
  CELERY_NODE_SERVICE_NAME="${SERVICE_NAME:-}"
  if [ -z "$CELERY_NODE_SERVICE_NAME" ]; then
    CELERY_NODE_SERVICE_NAME="embedded-$$"
  fi

  PYTHONPATH="$BASE_DIR${PYTHONPATH:+:$PYTHONPATH}" python -m celery -A config worker -l info --concurrency=2 -n "worker.${CELERY_NODE_SERVICE_NAME}@%h" &
  CELERY_WORKER_PID=$!
  record_pid_file "$CELERY_WORKER_PID" "$CELERY_WORKER_PID_FILE"
  PYTHONPATH="$BASE_DIR${PYTHONPATH:+:$PYTHONPATH}" python -m celery -A config beat -l info &
  CELERY_BEAT_PID=$!
  record_pid_file "$CELERY_BEAT_PID" "$CELERY_BEAT_PID_FILE"
elif [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ] && \
     [ -n "$SERVICE_NAME" ] && [ -f "$LOCK_DIR/celery.lck" ]; then
  # Keep dedicated Celery units aligned with the core service in systemd mode.
  arthexis_start_systemd_unit_if_present "celery-${SERVICE_NAME}.service"
  arthexis_start_systemd_unit_if_present "celery-beat-${SERVICE_NAME}.service"
fi
startup_phase_finish "celery_coordination"

if [ "$LCD_EMBEDDED" = true ]; then
  startup_phase_begin "lcd_launch"
  python -m apps.screens.lcd_screen.runner &
  LCD_PROCESS_PID=$!
  record_pid_file "$LCD_PROCESS_PID" "$LCD_PID_FILE"
  startup_phase_finish "lcd_launch"
fi

if [ "$AWAIT_START" = true ]; then
  startup_phase_begin "runserver_spawn"
  if [ "$RELOAD" = true ]; then
    python manage.py runserver 0.0.0.0:"$PORT" "${RUNSERVER_EXTRA_ARGS[@]}" &
  else
    python manage.py runserver 0.0.0.0:"$PORT" --noreload "${RUNSERVER_EXTRA_ARGS[@]}" &
  fi
  DJANGO_SERVER_PID=$!
  record_pid_file "$DJANGO_SERVER_PID" "$DJANGO_PID_FILE"
  startup_phase_finish "runserver_spawn"

  if wait_for_suite_startup_timed "$PORT" "$DJANGO_SERVER_PID" "$STARTUP_TIMEOUT"; then
    record_startup_duration 0
    arthexis_log_suite_uptime "$BASE_DIR" || true
    wait "$DJANGO_SERVER_PID"
  else
    record_startup_duration 1
    exit 1
  fi
else
  startup_phase_begin "runserver_spawn"
  if [ "$RELOAD" = true ]; then
    python manage.py runserver 0.0.0.0:"$PORT" "${RUNSERVER_EXTRA_ARGS[@]}" &
  else
    python manage.py runserver 0.0.0.0:"$PORT" --noreload "${RUNSERVER_EXTRA_ARGS[@]}" &
  fi
  DJANGO_SERVER_PID=$!
  record_pid_file "$DJANGO_SERVER_PID" "$DJANGO_PID_FILE"
  startup_phase_finish "runserver_spawn"
  (
    if wait_for_suite_startup_timed "$PORT" "$DJANGO_SERVER_PID" "$STARTUP_TIMEOUT"; then
      record_startup_duration 0
      arthexis_log_suite_uptime "$BASE_DIR" || true
    else
      record_startup_duration 1
    fi
  ) &
  wait "$DJANGO_SERVER_PID"
fi
