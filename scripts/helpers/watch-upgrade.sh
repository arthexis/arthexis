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
VENV_DIR="$BASE_DIR/.venv"
VENV_BIN="$VENV_DIR/bin"

if [ -z "${ARTHEXIS_PYTHON_BIN:-}" ] && [ -x "$VENV_BIN/python" ]; then
  export ARTHEXIS_PYTHON_BIN="$VENV_BIN/python"
fi

if [ -d "$VENV_DIR" ]; then
  export VIRTUAL_ENV="${VIRTUAL_ENV:-$VENV_DIR}"
  export PATH="$VENV_BIN:${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
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

ORCHESTRATOR="$BASE_DIR/scripts/helpers/predeploy-migrate-orchestrator.sh"

if [ ! -x "$ORCHESTRATOR" ]; then
  log "Deploy orchestrator missing at $ORCHESTRATOR"
  exit 1
fi

STATUS=0
(cd "$BASE_DIR" && "$ORCHESTRATOR" "$SERVICE_NAME" "${UPGRADE_CMD[@]}") || STATUS=$?

echo "$(date --iso-8601=seconds) Finished detached upgrade with status $STATUS" >&2
exit "$STATUS"
