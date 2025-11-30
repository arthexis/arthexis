#!/usr/bin/env bash
set -uo pipefail

SERVICE_NAME="${1:-}"
BASE_DIR="${ARTHEXIS_BASE_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
LOG_DIR="${ARTHEXIS_LOG_DIR:-$BASE_DIR/logs}"
LOG_FILE="$LOG_DIR/${SERVICE_NAME:-unknown}-watchdog.log"
LOCK_DIR="${ARTHEXIS_LOCK_DIR:-$BASE_DIR/.locks}"
SLEEP_INTERVAL=60
START_TIMEOUT_SECONDS=300
WATCHDOG_NOTE_FILE="/var/tmp/arthexis-watchdog-alerts.log"
WATCH_TARGETS=()

log() {
  local timestamp
  timestamp="$(date --iso-8601=seconds)"
  echo "$timestamp $*" >&2
}

control_with_sudo() {
  local action="$1"
  local unit="$2"
  local runner=(systemctl)

  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    runner=(sudo -n systemctl)
  fi

  if [ -n "$unit" ]; then
    "${runner[@]}" "$action" "$unit" 2>/dev/null
  else
    "${runner[@]}" "$action" 2>/dev/null
  fi
}

require_service_name() {
  if [ -z "$SERVICE_NAME" ]; then
    echo "Usage: $0 <service-name>" >&2
    exit 1
  fi
}

require_systemctl() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl is required for the watchdog" >&2
    exit 1
  fi
}

initialize_logging() {
  mkdir -p "$LOG_DIR"
  exec > >(tee -a "$LOG_FILE") 2>&1
  log "Logging to $LOG_FILE"
}

service_unit_exists() {
  local unit="${1:-${SERVICE_NAME}.service}"
  systemctl list-unit-files | awk '{print $1}' | grep -Fxq "$unit"
}

ensure_enabled() {
  local unit="${1:-${SERVICE_NAME}.service}"
  if ! systemctl is-enabled --quiet "$unit"; then
    log "${unit} is disabled; enabling."
    control_with_sudo enable "$unit" || true
  fi
}

attempt_start() {
  local unit="${1:-${SERVICE_NAME}.service}"
  log "${unit} is not active; attempting to start."
  control_with_sudo start "$unit" || true
}

leave_admin_notice() {
  local unit="$1"
  local message
  message="${SERVICE_NAME:-arthexis} watchdog could not restore ${unit} after ${START_TIMEOUT_SECONDS}s; manual intervention required."

  log "$message"

  if command -v logger >/dev/null 2>&1; then
    logger -t "${SERVICE_NAME:-arthexis}-watchdog" "$message" || true
  fi

  {
    date --iso-8601=seconds
    echo "$message"
    echo ""
  } >> "$WATCHDOG_NOTE_FILE"
}

read_watch_targets() {
  local lock_file
  lock_file="$LOCK_DIR/systemd_services.lck"

  if [ -f "$lock_file" ]; then
    mapfile -t WATCH_TARGETS < <(grep -E '\\.service$' "$lock_file" | grep -Ev "^$|watchdog\\.service$")
  fi

  if [ ${#WATCH_TARGETS[@]} -eq 0 ]; then
    WATCH_TARGETS=()
    if [ -n "$SERVICE_NAME" ]; then
      WATCH_TARGETS+=("${SERVICE_NAME}.service")
    fi
  fi
}

wait_for_active() {
  local unit="$1"
  local deadline
  deadline=$(( $(date +%s) + START_TIMEOUT_SECONDS ))

  while (( $(date +%s) < deadline )); do
    if systemctl is-active --quiet "$unit"; then
      return 0
    fi
    sleep 5
  done

  return 1
}

monitor_service() {
  local unit

  while true; do
    read_watch_targets

    if [ ${#WATCH_TARGETS[@]} -eq 0 ]; then
      log "No services registered for watchdog monitoring; retrying later."
      sleep "$SLEEP_INTERVAL"
      continue
    fi

    for unit in "${WATCH_TARGETS[@]}"; do
      if ! service_unit_exists "$unit"; then
        log "${unit} is not registered; skipping until it is available."
        continue
      fi

      ensure_enabled "$unit"

      if systemctl is-active --quiet "$unit"; then
        continue
      fi

      attempt_start "$unit"

      if wait_for_active "$unit"; then
        log "${unit} restored by watchdog."
        continue
      fi

      leave_admin_notice "$unit"
    done

    sleep "$SLEEP_INTERVAL"
  done
}

require_service_name
require_systemctl
initialize_logging
monitor_service
