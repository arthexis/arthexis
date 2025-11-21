#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${1:-}"
if [ -n "$SERVICE_NAME" ]; then
  shift
fi

BASE_DIR="${ARTHEXIS_BASE_DIR:-$(pwd)}"
LOG_DIR="${ARTHEXIS_LOG_DIR:-$BASE_DIR/logs}"
LOG_FILE="${LOG_DIR}/watch-upgrade.log"

mkdir -p "$LOG_DIR"

if [ ! -d "$BASE_DIR" ]; then
  echo "Base directory $BASE_DIR does not exist" >>"$LOG_FILE"
  exit 1
fi

exec > >(tee -a "$LOG_FILE") 2>&1

echo "$(date --iso-8601=seconds) Starting detached upgrade for ${SERVICE_NAME:-<unknown>} in ${BASE_DIR}" >&2

UPGRADE_CMD=("$BASE_DIR/upgrade.sh" "--stable")
if [ "$#" -gt 0 ]; then
  UPGRADE_CMD=("$@")
fi

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

  "${runner[@]}" "$action" "$unit" || true
}

control_with_sudo stop "$SERVICE_NAME"

STATUS=0
if [ -x "${UPGRADE_CMD[0]}" ]; then
  (cd "$BASE_DIR" && "${UPGRADE_CMD[@]}") || STATUS=$?
else
  echo "Upgrade command ${UPGRADE_CMD[*]} is not executable" >&2
  STATUS=1
fi

control_with_sudo start "$SERVICE_NAME"

echo "$(date --iso-8601=seconds) Finished detached upgrade with status $STATUS" >&2
exit "$STATUS"
