#!/usr/bin/env bash
set -uo pipefail

SERVICE_NAME="${1:-}"
BASE_DIR="${ARTHEXIS_BASE_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
LOG_DIR="${ARTHEXIS_LOG_DIR:-$BASE_DIR/logs}"
LOG_FILE="$LOG_DIR/${SERVICE_NAME:-unknown}-watchdog.log"
SLEEP_INTERVAL=30
DOWN_THRESHOLD_SECONDS=600

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
  systemctl list-unit-files | awk '{print $1}' | grep -Fxq "${SERVICE_NAME}.service"
}

ensure_enabled() {
  if ! systemctl is-enabled --quiet "$SERVICE_NAME"; then
    log "${SERVICE_NAME} is disabled; enabling."
    control_with_sudo enable "$SERVICE_NAME" || true
  fi
}

attempt_start() {
  log "${SERVICE_NAME} is not active; attempting to start."
  control_with_sudo start "$SERVICE_NAME" || true
}

restart_system() {
  log "${SERVICE_NAME} has been down for more than ${DOWN_THRESHOLD_SECONDS}s after restart attempts; rebooting host."
  control_with_sudo reboot "" || systemctl reboot || sudo reboot || reboot
}

monitor_service() {
  local down_since=0

  while true; do
    if ! service_unit_exists; then
      log "${SERVICE_NAME}.service is not registered; waiting for installation."
      sleep "$SLEEP_INTERVAL"
      continue
    fi

    ensure_enabled

    if systemctl is-active --quiet "$SERVICE_NAME"; then
      down_since=0
    else
      if [ "$down_since" -eq 0 ]; then
        down_since=$(date +%s)
      fi
      attempt_start
      local now
      now=$(date +%s)
      local downtime
      downtime=$((now - down_since))
      if [ "$downtime" -ge "$DOWN_THRESHOLD_SECONDS" ]; then
        restart_system
      fi
    fi

    sleep "$SLEEP_INTERVAL"
  done
}

require_service_name
require_systemctl
initialize_logging
monitor_service
