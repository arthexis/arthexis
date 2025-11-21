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

log() {
  echo "$(date --iso-8601=seconds) $*" >&2
}

on_error() {
  local exit_code=$?
  log "Upgrade failed at command: ${BASH_COMMAND:-<unknown>} (exit ${exit_code})"
}

trap on_error ERR
set -o errtrace

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

control_with_sudo start "$SERVICE_NAME"

echo "$(date --iso-8601=seconds) Finished detached upgrade with status $STATUS" >&2
exit "$STATUS"
