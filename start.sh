#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK_DIR="$BASE_DIR/locks"
SKIP_LOCK="$LOCK_DIR/service-start-skip.lck"
mkdir -p "$LOCK_DIR"

SILENT=false
SERVICE_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --silent)
      SILENT=true
      shift
      ;;
    *)
      SERVICE_ARGS+=("$1")
      shift
      ;;
  esac
done

echo "Manual start requested; creating one-time skip lock for upgrade checks." \
  >>"$BASE_DIR/logs/start.log" 2>/dev/null || true
# Create a short-lived lock so the upcoming start skips upgrade once.
date +%s > "$SKIP_LOCK"

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

if [ -n "$SERVICE_NAME" ] && [ ${#SYSTEMCTL_CMD[@]} -gt 0 ] \
  && "${SYSTEMCTL_CMD[@]}" list-unit-files | grep -Fq "${SERVICE_NAME}.service"; then
  "${SYSTEMCTL_CMD[@]}" restart "$SERVICE_NAME"
  if [ "$SILENT" = true ]; then
    exit 0
  fi
  if wait_for_systemd_service "$SERVICE_NAME"; then
    exit 0
  else
    exit 1
  fi
fi

exec "$BASE_DIR/service-start.sh" "${SERVICE_ARGS[@]}"
