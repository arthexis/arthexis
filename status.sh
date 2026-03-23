#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
STARTUP_TIMEOUT=300
STATUS_WAIT_INTERVAL=2
STATUS_WAIT_TIMEOUT=60
exit_code=0
WAIT_FOR_REACHABLE=false

usage() {
  cat <<'EOF'
Usage: ./status.sh [options]

Options:
  -h, --help  Show this help message and exit.
  --wait  Keep polling when the app is not reachable yet and exit after it becomes reachable.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --wait)
      WAIT_FOR_REACHABLE=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
  shift
done

# shellcheck source=scripts/helpers/env.sh
. "$BASE_DIR/scripts/helpers/env.sh"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/ports.sh
. "$BASE_DIR/scripts/helpers/ports.sh"
# shellcheck source=scripts/helpers/service_manager.sh
. "$BASE_DIR/scripts/helpers/service_manager.sh"
arthexis_load_env_file "$BASE_DIR"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
# Always read startup errors from the repository log directory so test fixtures
# that seed the log file are honored.
ERROR_LOG="$BASE_DIR/logs/error.log"
mkdir -p "$(dirname "$ERROR_LOG")"

arthexis_suite_reachable() {
  local port="$1"
  if [ -z "$port" ]; then
    return 1
  fi

  local python_bin
  if command -v python3 >/dev/null 2>&1; then
    python_bin=python3
  elif command -v python >/dev/null 2>&1; then
    python_bin=python
  else
    return 1
  fi

  PYTHONPATH="$BASE_DIR" "$python_bin" -m utils.service_probe probe-admin-login --port "$port" >/dev/null 2>&1
}

arthexis_read_startup_timestamp() {
  if [ ! -f "$STARTUP_LOCK" ]; then
    return 1
  fi

  local started_at
  started_at=$(tr -d '\r' < "$STARTUP_LOCK" | head -n1)
  if ! printf '%s' "$started_at" | grep -Eq '^[0-9]+$'; then
    return 1
  fi

  printf '%s' "$started_at"
}

LOCK_DIR="$BASE_DIR/.locks"
STARTUP_LOCK="$LOCK_DIR/startup_started_at.lck"
SERVICE_MANAGEMENT_MODE="$(arthexis_detect_service_mode "$LOCK_DIR")"
UPGRADE_IN_PROGRESS_LOCK="$LOCK_DIR/upgrade_in_progress.lck"

# Determine installation status
if [ -d "$BASE_DIR/.venv" ]; then
  INSTALLED=true
else
  INSTALLED=false
fi

echo "Application installed: $INSTALLED"

VERSION=""
if [ -f "$BASE_DIR/VERSION" ]; then
  VERSION="$(tr -d '\r\n' < "$BASE_DIR/VERSION")"
fi

REVISION=""
if command -v python3 >/dev/null 2>&1; then
  REVISION="$(
    PYTHONPATH="$BASE_DIR" BASE_DIR="$BASE_DIR" python3 - <<'PY' 2>/dev/null || true
import os
import sys

base_dir = os.environ.get("BASE_DIR", "")
if base_dir:
    sys.path.insert(0, base_dir)

try:
    from utils import revision
except Exception:
    print("")
else:
    print(revision.get_revision())
PY
  )"
fi

if [ -z "$REVISION" ] && command -v git >/dev/null 2>&1; then
  REVISION="$(git -C "$BASE_DIR" rev-parse HEAD 2>/dev/null || true)"
fi

if [ -z "$REVISION" ] && [ -f "$BASE_DIR/.revision" ]; then
  REVISION="$(cat "$BASE_DIR/.revision")"
fi

echo "Version: $VERSION"
echo "Revision: $REVISION"
SHORT_REVISION=""
if [ -n "$REVISION" ]; then
  SHORT_REVISION="${REVISION:0:7}"
fi
echo "Short Revision: $SHORT_REVISION"

if [ -f "$UPGRADE_IN_PROGRESS_LOCK" ]; then
  STARTED_AT=$(tr -d '\r' < "$UPGRADE_IN_PROGRESS_LOCK" | head -n1)
  if [ -n "$STARTED_AT" ]; then
    echo "Upgrade status: in progress (started at $STARTED_AT)"
  else
    echo "Upgrade status: in progress"
  fi
else
  echo "Upgrade status: idle"
fi

SERVICE=""
if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE="$(cat "$LOCK_DIR/service.lck")"
  if [ -n "$SERVICE" ]; then
    if printf '%s' "$SERVICE" | grep -Eq '^-'; then
      echo "Invalid service name detected: $SERVICE"
      echo "Service names must not begin with a dash to avoid unsafe systemd unit creation."
      exit 1
    fi
    if ! printf '%s' "$SERVICE" | grep -Eq '^[A-Za-z0-9_.-]+$'; then
      echo "Invalid service name detected: $SERVICE"
      echo "Service names may only contain letters, numbers, underscores, dots, and hyphens."
      echo "Refusing to continue to avoid creating or managing unsafe systemd units."
      exit 1
    fi
  fi
  echo "Service: $SERVICE"
else
  echo "Service: not installed"
fi

# Determine configured port
CONFIGURED_PORT="$(arthexis_detect_backend_port "$BASE_DIR")"
PORT="$CONFIGURED_PORT"

echo "Configured port: $CONFIGURED_PORT"

ROLE="${NODE_ROLE:-Terminal}"
if [ -z "$NODE_ROLE" ] && [ -f "$LOCK_DIR/role.lck" ]; then
  ROLE="$(cat "$LOCK_DIR/role.lck")"
fi
echo "Node role: $ROLE"

# Features
if [ -f "$LOCK_DIR/celery.lck" ]; then
  CELERY_FEATURE=true
else
  CELERY_FEATURE=false
fi
if arthexis_lcd_feature_enabled "$LOCK_DIR"; then
  LCD_FEATURE=true
else
  LCD_FEATURE=false
fi
echo "Features:"
echo "  Celery: $CELERY_FEATURE"
echo "  LCD screen: $LCD_FEATURE"

echo "Checking running status..."
RUNNING=false
if [ -n "$SERVICE" ] && command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -Fq -- "${SERVICE}.service"; then
  STATUS=$(systemctl is-active -- "$SERVICE" || true)
  echo "  Service status: $STATUS"
  [ "$STATUS" = "active" ] && RUNNING=true
  if LIVE_PORT="$(arthexis_detect_live_runserver_port "$BASE_DIR")"; then
    PORT="$LIVE_PORT"
  fi
else
  if LIVE_PORT="$(arthexis_detect_live_runserver_port "$BASE_DIR")"; then
    RUNNING=true
    PORT="$LIVE_PORT"
  fi
  echo "  Process running: $RUNNING"
fi

SUITE_REACHABLE=false
if arthexis_suite_reachable "$PORT"; then
  SUITE_REACHABLE=true
fi

if [ "$SUITE_REACHABLE" = true ]; then
  echo "Application reachable at: http://localhost:$PORT"
elif [ "$RUNNING" = true ]; then
  echo "Application process running but port $PORT is not reachable yet"
else
  echo "Application is not running"
fi

if [ "$WAIT_FOR_REACHABLE" = true ] && [ "$SUITE_REACHABLE" = false ]; then
  echo "Waiting for application to become reachable on port $PORT..."
  WAIT_STARTED_AT=$(date +%s)
  while true; do
    WAIT_NOW=$(date +%s)
    WAIT_ELAPSED=$((WAIT_NOW - WAIT_STARTED_AT))
    if [ "$WAIT_ELAPSED" -ge "$STATUS_WAIT_TIMEOUT" ]; then
      echo "Timed out waiting for reachability after ${STATUS_WAIT_TIMEOUT}s."
      exit_code=1
      break
    fi

    if STARTED_AT=$(arthexis_read_startup_timestamp); then
      ELAPSED=$((WAIT_NOW - STARTED_AT))
      if [ "$ELAPSED" -ge "$STARTUP_TIMEOUT" ]; then
        echo "Startup failed: suite not reachable after ${STARTUP_TIMEOUT}s."
        exit_code=1
        break
      fi
    fi

    sleep "$STATUS_WAIT_INTERVAL"
    if arthexis_suite_reachable "$PORT"; then
      SUITE_REACHABLE=true
      echo "Application reachable at: http://localhost:$PORT"
      break
    fi
    echo "Still waiting for application to become reachable on port $PORT..."
  done
fi

if STARTED_AT=$(arthexis_read_startup_timestamp); then
  NOW=$(date +%s)
  ELAPSED=$((NOW - STARTED_AT))

  if [ "$SUITE_REACHABLE" = true ]; then
    echo "Startup completed after ${ELAPSED}s; clearing startup lock."
    rm -f "$STARTUP_LOCK"
  elif [ "$ELAPSED" -lt "$STARTUP_TIMEOUT" ]; then
    echo "Startup in progress: suite not reachable yet (${ELAPSED}s elapsed, waiting up to ${STARTUP_TIMEOUT}s)."
  else
    echo "Startup failed: suite not reachable after ${STARTUP_TIMEOUT}s."
    if [ -s "$ERROR_LOG" ]; then
      echo "Recent errors from $ERROR_LOG:"
      tail -n 40 "$ERROR_LOG"
    elif [ -f "$ERROR_LOG" ]; then
      echo "No errors captured in $ERROR_LOG."
    else
      echo "Error log not found at $ERROR_LOG."
    fi
    exit_code=1
  fi
fi

# Celery status
if [ "$CELERY_FEATURE" = true ]; then
  if [ -n "$SERVICE" ] && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ] && \
     command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -Fq -- "celery-$SERVICE.service"; then
    C_STATUS=$(systemctl is-active -- "celery-$SERVICE" || true)
    B_STATUS=$(systemctl is-active -- "celery-beat-$SERVICE" || true)
    echo "  Celery worker status: $C_STATUS"
    echo "  Celery beat status: $B_STATUS"
  else
    if pgrep -f "celery -A config" >/dev/null 2>&1; then
      echo "  Celery processes: running"
    else
      echo "  Celery processes: not running"
    fi
  fi
fi

if [ "$LCD_FEATURE" = true ]; then
  if [ -n "$SERVICE" ] && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ] && \
     command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -Fq -- "lcd-$SERVICE.service"; then
    LCD_STATUS=$(systemctl is-active -- "lcd-$SERVICE" || true)
    echo "  LCD screen service status: $LCD_STATUS"
  else
    if pgrep -f "python -m apps\\.screens\\.lcd_screen\\.runner" >/dev/null 2>&1; then
      echo "  LCD screen process: running"
    else
      echo "  LCD screen process: not running"
    fi
  fi
fi

# Node information
if command -v hostname >/dev/null 2>&1; then
  echo "Hostname: $(hostname)"
  echo "IP addresses: $(hostname -I 2>/dev/null || echo 'N/A')"
fi

exit "$exit_code"
