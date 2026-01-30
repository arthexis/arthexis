#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK_DIR="$BASE_DIR/.locks"
mkdir -p "$LOCK_DIR"

# shellcheck source=scripts/helpers/env.sh
. "$BASE_DIR/scripts/helpers/env.sh"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/suite-uptime-lock.sh
. "$BASE_DIR/scripts/helpers/suite-uptime-lock.sh"
arthexis_load_env_file "$BASE_DIR"
STARTUP_SCRIPT_NAME="$(basename "$0")"
arthexis_log_startup_event "$BASE_DIR" "$STARTUP_SCRIPT_NAME" "start" "invoked"

log_startup_exit() {
  local status=$?
  arthexis_log_startup_event "$BASE_DIR" "$STARTUP_SCRIPT_NAME" "finish" "status=$status"
}
trap log_startup_exit EXIT

refresh_suite_uptime_lock_safe() {
  arthexis_refresh_suite_uptime_lock "$BASE_DIR" || true
}

SILENT=false
DEBUG_MODE=false
SHOW_LEVEL=""
SERVICE_ARGS=()
RELOAD_REQUESTED=false
CLEAR_LOGS=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --silent)
      SILENT=true
      shift
      ;;
    --reload)
      RELOAD_REQUESTED=true
      SERVICE_ARGS+=("$1")
      shift
      ;;
    --debug)
      DEBUG_MODE=true
      SERVICE_ARGS+=("$1")
      shift
      ;;
    --show)
      if [ -z "${2:-}" ]; then
        echo "Usage: $0 [--silent] [--debug] [--show LEVEL] [--log-follow] [--clear-logs] [service args...]" >&2
        exit 1
      fi
      SHOW_LEVEL="$2"
      SERVICE_ARGS+=("$1" "$2")
      shift 2
      ;;
    --log-follow)
      SERVICE_ARGS+=("$1")
      shift
      ;;
    --clear-logs)
      CLEAR_LOGS=true
      shift
      ;;
    *)
      SERVICE_ARGS+=("$1")
      shift
      ;;
  esac
done

if [ -n "$SHOW_LEVEL" ] && [ "${SHOW_LEVEL^^}" = "DEBUG" ]; then
  DEBUG_MODE=true
fi

if [ "$CLEAR_LOGS" = true ]; then
  if [ -x "$BASE_DIR/stop.sh" ]; then
    STOP_ARGS=()
    for arg in "${SERVICE_ARGS[@]}"; do
      case "$arg" in
        --all|--force)
          STOP_ARGS+=("$arg")
          ;;
        --*)
          ;;
        *)
          STOP_ARGS+=("$arg")
          ;;
      esac
    done
    echo "Stopping services before clearing logs..."
    if ! "$BASE_DIR/stop.sh" "${STOP_ARGS[@]}"; then
      echo "Unable to stop services; refusing to clear logs." >&2
      exit 1
    fi
  fi
  arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
  arthexis_clear_log_files "$BASE_DIR" "$LOG_DIR" ""
fi

echo "Manual start requested." >>"$BASE_DIR/logs/start.log" 2>/dev/null || true

SYSTEMCTL_CMD=()
if command -v systemctl >/dev/null 2>&1; then
  SYSTEMCTL_CMD=(systemctl)
  if command -v sudo >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null; then
      SYSTEMCTL_CMD=(sudo -n systemctl)
    elif [ "$(id -u)" -ne 0 ]; then
      SYSTEMCTL_CMD=(systemctl)
    fi
  fi
fi

wait_for_systemd_service() {
  local service_name="$1"
  local timeout=120
  local start_time
  start_time=$(date +%s)
  local last_summary=""

  echo "Waiting for systemd unit '$service_name' to start (timeout ${timeout}s)..."
  while true; do
    local active_state
    local sub_state
    local result_state
    active_state=$("${SYSTEMCTL_CMD[@]}" show "$service_name" --property=ActiveState --value 2>/dev/null || echo "unknown")
    sub_state=$("${SYSTEMCTL_CMD[@]}" show "$service_name" --property=SubState --value 2>/dev/null || echo "unknown")
    result_state=$("${SYSTEMCTL_CMD[@]}" show "$service_name" --property=Result --value 2>/dev/null || echo "unknown")
    local summary
    summary="state=${active_state}, substate=${sub_state}, result=${result_state}"

    if [ "$summary" != "$last_summary" ]; then
      printf '[%s] %s\n' "$(date +%H:%M:%S)" "$summary"
      last_summary="$summary"
    fi

    if [ "$active_state" = "active" ]; then
      echo "Service '$service_name' is active."
      "${SYSTEMCTL_CMD[@]}" status "$service_name" --no-pager --lines 10 || true
      return 0
    fi

    if [ "$active_state" = "failed" ] || [ "$result_state" = "failed" ]; then
      echo "Service '$service_name' failed during startup."
      "${SYSTEMCTL_CMD[@]}" status "$service_name" --no-pager --lines 20 || true
      if command -v journalctl >/dev/null 2>&1; then
        journalctl -u "$service_name" -n 20 --no-pager || true
      fi
      return 1
    fi

    if [ $(( $(date +%s) - start_time )) -ge $timeout ]; then
      echo "Timed out after ${timeout}s waiting for '$service_name' to reach active state."
      "${SYSTEMCTL_CMD[@]}" status "$service_name" --no-pager --lines 20 || true
      return 1
    fi

    sleep 2
  done
}

SERVICE_NAME=""
if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE_NAME="$(tr -d '\r\n' < "$LOCK_DIR/service.lck")"
fi
RFID_SERVICE_LOCK="$LOCK_DIR/rfid-service.lck"
RFID_SERVICE_CONFIGURED=false
if [ -f "$RFID_SERVICE_LOCK" ]; then
  RFID_SERVICE_CONFIGURED=true
fi
CAMERA_SERVICE_LOCK="$LOCK_DIR/camera-service.lck"
CAMERA_SERVICE_CONFIGURED=false
if [ -f "$CAMERA_SERVICE_LOCK" ]; then
  CAMERA_SERVICE_CONFIGURED=true
fi
RFID_SERVICE_UNIT=""
RFID_UNIT_PRESENT=false
if [ -n "$SERVICE_NAME" ]; then
  RFID_SERVICE_UNIT="rfid-$SERVICE_NAME"
fi
if [ ${#SYSTEMCTL_CMD[@]} -gt 0 ] && [ -n "$RFID_SERVICE_UNIT" ] && \
  "${SYSTEMCTL_CMD[@]}" list-unit-files | grep -Fq "${RFID_SERVICE_UNIT}.service"; then
  RFID_UNIT_PRESENT=true
fi
CAMERA_SERVICE_UNIT=""
CAMERA_UNIT_PRESENT=false
if [ -n "$SERVICE_NAME" ]; then
  CAMERA_SERVICE_UNIT="camera-$SERVICE_NAME"
fi
if [ ${#SYSTEMCTL_CMD[@]} -gt 0 ] && [ -n "$CAMERA_SERVICE_UNIT" ] && \
  "${SYSTEMCTL_CMD[@]}" list-unit-files | grep -Fq "${CAMERA_SERVICE_UNIT}.service"; then
  CAMERA_UNIT_PRESENT=true
fi

if [ "$RFID_SERVICE_CONFIGURED" = true ]; then
  RFID_INITIAL_STATUS="unknown"
  if [ ${#SYSTEMCTL_CMD[@]} -eq 0 ]; then
    RFID_INITIAL_STATUS="systemctl-unavailable"
  elif [ "$RFID_UNIT_PRESENT" = true ]; then
    RFID_INITIAL_STATUS=$("${SYSTEMCTL_CMD[@]}" is-active "$RFID_SERVICE_UNIT" 2>/dev/null || echo "unknown")
  else
    RFID_INITIAL_STATUS="not-registered"
  fi
  echo "RFID service initial status: $RFID_INITIAL_STATUS"
  arthexis_log_startup_event "$BASE_DIR" "$STARTUP_SCRIPT_NAME" "rfid-status" "initial_status=$RFID_INITIAL_STATUS"
fi
if [ "$CAMERA_SERVICE_CONFIGURED" = true ]; then
  CAMERA_INITIAL_STATUS="unknown"
  if [ ${#SYSTEMCTL_CMD[@]} -eq 0 ]; then
    CAMERA_INITIAL_STATUS="systemctl-unavailable"
  elif [ "$CAMERA_UNIT_PRESENT" = true ]; then
    CAMERA_INITIAL_STATUS=$("${SYSTEMCTL_CMD[@]}" is-active "$CAMERA_SERVICE_UNIT" 2>/dev/null || echo "unknown")
  else
    CAMERA_INITIAL_STATUS="not-registered"
  fi
  echo "Camera service initial status: $CAMERA_INITIAL_STATUS"
  arthexis_log_startup_event "$BASE_DIR" "$STARTUP_SCRIPT_NAME" "camera-status" "initial_status=$CAMERA_INITIAL_STATUS"
fi

if [ "$DEBUG_MODE" = false ] && [ -z "$SHOW_LEVEL" ] && [ "$RELOAD_REQUESTED" = false ] \
  && [ -n "$SERVICE_NAME" ] && [ ${#SYSTEMCTL_CMD[@]} -gt 0 ] \
  && "${SYSTEMCTL_CMD[@]}" list-unit-files | grep -Fq "${SERVICE_NAME}.service"; then
  "${SYSTEMCTL_CMD[@]}" restart "$SERVICE_NAME"
  if [ "$RFID_SERVICE_CONFIGURED" = true ] && [ "$RFID_UNIT_PRESENT" = true ]; then
    "${SYSTEMCTL_CMD[@]}" restart "$RFID_SERVICE_UNIT"
  fi
  if [ "$CAMERA_SERVICE_CONFIGURED" = true ] && [ "$CAMERA_UNIT_PRESENT" = true ]; then
    "${SYSTEMCTL_CMD[@]}" restart "$CAMERA_SERVICE_UNIT"
  fi
  if [ "$SILENT" = true ]; then
    exit 0
  fi
  if wait_for_systemd_service "$SERVICE_NAME"; then
    if [ "$RFID_SERVICE_CONFIGURED" = true ] && [ "$RFID_UNIT_PRESENT" = true ]; then
      if ! wait_for_systemd_service "$RFID_SERVICE_UNIT"; then
        exit 1
      fi
    fi
    if [ "$CAMERA_SERVICE_CONFIGURED" = true ] && [ "$CAMERA_UNIT_PRESENT" = true ]; then
      if ! wait_for_systemd_service "$CAMERA_SERVICE_UNIT"; then
        exit 1
      fi
    fi
    refresh_suite_uptime_lock_safe
    exit 0
  else
    exit 1
  fi
fi

if "$BASE_DIR/scripts/service-start.sh" "${SERVICE_ARGS[@]}"; then
  refresh_suite_uptime_lock_safe
fi
