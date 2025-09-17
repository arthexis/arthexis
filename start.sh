#!/usr/bin/env bash
set -e
set -E
set -o pipefail

ARTHEXIS_TRACE_CURRENT=""
ARTHEXIS_REPORTING=0
declare -a ARTHEXIS_CELERY_PIDS=()

trap 'ARTHEXIS_TRACE_CURRENT=$BASH_COMMAND' DEBUG

cleanup_background_processes() {
  if [ "${#ARTHEXIS_CELERY_PIDS[@]}" -eq 0 ]; then
    return
  fi

  for pid in "${ARTHEXIS_CELERY_PIDS[@]}"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}

report_start_failure() {
  local exit_code="$1"
  if [ "$exit_code" -eq 0 ] || [ "$ARTHEXIS_REPORTING" -ne 0 ]; then
    return
  fi

  ARTHEXIS_REPORTING=1

  local failed_command
  failed_command="${ARTHEXIS_TRACE_CURRENT:-${BASH_COMMAND:-unknown}}"
  local host
  host="$(hostname 2>/dev/null || echo "unknown")"

  local version="unknown"
  if [ -f "$BASE_DIR/VERSION" ]; then
    version="$(tr -d '\r' <"$BASE_DIR/VERSION" | head -n 1)"
  fi

  local python_bin=""
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    python_bin="$VIRTUAL_ENV/bin/python"
  elif [ -x "$BASE_DIR/.venv/bin/python" ]; then
    python_bin="$BASE_DIR/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    python_bin="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    python_bin="$(command -v python)"
  fi

  local revision=""
  if [ -n "$python_bin" ]; then
    revision="$($python_bin - <<'PY' 2>/dev/null || true
from utils.revision import get_revision
print(get_revision())
PY
)"
    revision="${revision%%$'\n'*}"
  fi
  if [ -z "$revision" ] && command -v git >/dev/null 2>&1; then
    revision="$(git rev-parse HEAD 2>/dev/null || true)"
    revision="${revision%%$'\n'*}"
  fi

  if [ -z "$python_bin" ]; then
    echo "start.sh failed with exit code $exit_code while running: $failed_command" >&2
    echo "Unable to report the failure automatically because Python is unavailable." >&2
    return
  fi

  if [ -f "$LOG_FILE" ]; then
    echo "----- Last 100 lines from $LOG_FILE -----" >&2
    tail -n 100 "$LOG_FILE" >&2 || true
    echo "----------------------------------------" >&2
  fi

  local -a report_args=(
    "$python_bin" manage.py report_issue
    --source start
    --command "$failed_command"
    --exit-code "$exit_code"
    --host "$host"
    --app-version "$version"
    --revision "$revision"
  )
  if [ -f "$LOG_FILE" ]; then
    report_args+=(--log-file "$LOG_FILE")
  fi

  "${report_args[@]}" >/dev/null 2>&1 || echo "Failed to queue GitHub issue for start failure." >&2
}

arthexis_exit_trap() {
  local exit_code="$1"
  report_start_failure "$exit_code"
  cleanup_background_processes
}

trap 'report_start_failure $?' ERR
trap 'arthexis_exit_trap $?' EXIT

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

# Collect static files before starting services
python manage.py collectstatic --noinput

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

# Start Celery components to handle queued email if enabled
if [ "$CELERY" = true ]; then
  celery -A config worker -l info &
  CELERY_WORKER_PID=$!
  celery -A config beat -l info &
  CELERY_BEAT_PID=$!
  ARTHEXIS_CELERY_PIDS=()
  ARTHEXIS_CELERY_PIDS+=("$CELERY_WORKER_PID" "$CELERY_BEAT_PID")
fi

# Start the Django development server
if [ "$RELOAD" = true ]; then
  python manage.py runserver 0.0.0.0:"$PORT"
else
  python manage.py runserver 0.0.0.0:"$PORT" --noreload
fi
