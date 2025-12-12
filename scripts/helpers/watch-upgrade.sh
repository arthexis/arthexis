#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME=""
if [ "$#" -gt 0 ]; then
  first_arg="$1"

  if [[ ! "$first_arg" =~ ^-- ]] && [ ! -f "$first_arg" ] && [[ "$first_arg" != /* ]] && [[ "$first_arg" != ./* ]] && [[ "$first_arg" != ../* ]]; then
    SERVICE_NAME="$first_arg"
    shift
  fi
fi

BASE_DIR="${ARTHEXIS_BASE_DIR:-$(pwd)}"
LOG_DIR="${ARTHEXIS_LOG_DIR:-$BASE_DIR/logs}"
LOG_FILE="${LOG_DIR}/watch-upgrade.log"
NGINX_DISABLED_LOCK="${BASE_DIR}/.locks/nginx_disabled.lck"
BACKEND_PORT_LOCK="${BASE_DIR}/.locks/backend_port.lck"
UPGRADE_HOLDER_UNIT=""

SYSTEMCTL_CMD=()
if command -v systemctl >/dev/null 2>&1; then
  SYSTEMCTL_CMD=(systemctl)
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    SYSTEMCTL_CMD=(sudo -n systemctl)
  fi
fi

SYSTEMD_RUN_CMD=()
if command -v systemd-run >/dev/null 2>&1; then
  SYSTEMD_RUN_CMD=(systemd-run)
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    SYSTEMD_RUN_CMD=(sudo -n systemd-run)
  fi
fi

log() {
  echo "$(date --iso-8601=seconds) $*" >&2
}

on_error() {
  local exit_code=$?
  log "Upgrade failed at command: ${BASH_COMMAND:-<unknown>} (exit ${exit_code})"
}

trap on_error ERR
set -o errtrace
trap stop_upgrade_holder EXIT

mkdir -p "$LOG_DIR"

if [ ! -d "$BASE_DIR" ]; then
  log "Base directory $BASE_DIR does not exist; logging to $LOG_FILE"
  exit 1
fi

exec > >(tee -a "$LOG_FILE") 2>&1

log "Logging to $LOG_FILE"
log "Starting detached upgrade for ${SERVICE_NAME:-<unknown>} in ${BASE_DIR}"

UPGRADE_CMD=("$BASE_DIR/upgrade.sh" "--stable")
if [ "$#" -gt 0 ]; then
  UPGRADE_CMD=("$@")
fi

resolve_upgrade_command() {
  local raw="$1"

  if [[ "$raw" == /* ]]; then
    echo "$raw"
    return
  fi

  if [[ "$raw" == ./* || "$raw" == ../* || "$raw" == */* || -f "$BASE_DIR/$raw" ]]; then
    echo "$BASE_DIR/${raw#./}"
    return
  fi

  echo "$raw"
}

UPGRADE_CMD[0]="$(resolve_upgrade_command "${UPGRADE_CMD[0]}")"

log "Upgrade command: ${UPGRADE_CMD[*]}"

detect_backend_port() {
  local fallback=8888

  if [ -f "$BACKEND_PORT_LOCK" ]; then
    local value
    value="$(cat "$BACKEND_PORT_LOCK" | tr -d '\r\n[:space:]')"
    if [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -ge 1 ] && [ "$value" -le 65535 ]; then
      echo "$value"
      return
    fi
  fi

  echo "$fallback"
}

start_upgrade_holder() {
  if [ -z "$SERVICE_NAME" ]; then
    return 0
  fi

  if [ -f "$NGINX_DISABLED_LOCK" ]; then
    log "nginx management disabled; skipping upgrade holder"
    return 0
  fi

  if [ ${#SYSTEMD_RUN_CMD[@]} -eq 0 ]; then
    log "systemd-run unavailable; skipping upgrade holder"
    return 0
  fi

  if [ ! -x "$BASE_DIR/scripts/upgrade_holder.py" ]; then
    log "upgrade_holder script missing or not executable; skipping"
    return 0
  fi

  local port
  port="$(detect_backend_port)"
  local unit
  unit="upgrade-holder-${SERVICE_NAME}-$(date +%s)"
  local message
  message="${UPGRADE_HOLDER_MESSAGE:-Arthexis is upgrading. This page will refresh automatically when the service is ready.}"
  local refresh_seconds
  refresh_seconds="${UPGRADE_HOLDER_REFRESH_SECONDS:-5}"

  log "Starting upgrade holder ${unit} on port ${port}"

  if "${SYSTEMD_RUN_CMD[@]}" --unit "$unit" --description "Arthexis upgrade holder" \
    --property "WorkingDirectory=$BASE_DIR" \
    "$BASE_DIR/scripts/upgrade_holder.py" --port "$port" --message "$message" \
    --refresh-seconds "$refresh_seconds"; then
    UPGRADE_HOLDER_UNIT="${unit}.service"
  else
    log "Failed to launch upgrade holder unit ${unit}"
  fi
}

stop_upgrade_holder() {
  if [ -z "$UPGRADE_HOLDER_UNIT" ] || [ ${#SYSTEMCTL_CMD[@]} -eq 0 ]; then
    return 0
  fi

  log "Stopping upgrade holder ${UPGRADE_HOLDER_UNIT}"
  "${SYSTEMCTL_CMD[@]}" stop "$UPGRADE_HOLDER_UNIT" || true
  UPGRADE_HOLDER_UNIT=""
}

control_with_sudo() {
  local action="$1"
  local unit="$2"

  if [ -z "$unit" ] || ! command -v systemctl >/dev/null 2>&1; then
    return 0
  fi

  local runner=(systemctl)
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    runner=(sudo -n systemctl)
  fi

  if [ -n "$unit" ]; then
    log "systemctl ${action} ${unit}"
  fi

  "${runner[@]}" "$action" "$unit" || true
}

control_with_sudo stop "$SERVICE_NAME"
start_upgrade_holder

STATUS=0
if [ ! -x "${UPGRADE_CMD[0]}" ] && [ -f "${UPGRADE_CMD[0]}" ]; then
  chmod +x "${UPGRADE_CMD[0]}" 2>/dev/null || true
fi

if [ -x "${UPGRADE_CMD[0]}" ]; then
  (cd "$BASE_DIR" && "${UPGRADE_CMD[@]}") || STATUS=$?
else
  echo "Upgrade command ${UPGRADE_CMD[*]} is not executable" >&2
  STATUS=1
fi

stop_upgrade_holder
control_with_sudo start "$SERVICE_NAME"

echo "$(date --iso-8601=seconds) Finished detached upgrade with status $STATUS" >&2
exit "$STATUS"
