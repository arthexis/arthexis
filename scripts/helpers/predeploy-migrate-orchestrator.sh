#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${ARTHEXIS_BASE_DIR:-$(pwd)}"
LOCK_DIR="${BASE_DIR}/.locks"
LOG_DIR="${ARTHEXIS_LOG_DIR:-$BASE_DIR/logs}"
MIGRATION_MARKER_FILE="${LOCK_DIR}/predeploy_migrate_success.json"
SERVICE_NAME="${1:-}"

if [ "$#" -gt 0 ]; then
  shift
fi

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <service-name|''> <deploy command...>" >&2
  exit 1
fi

DEPLOY_CMD=("$@")

# shellcheck source=scripts/helpers/common.sh
. "$BASE_DIR/scripts/helpers/common.sh"
# shellcheck source=scripts/helpers/service_manager.sh
. "$BASE_DIR/scripts/helpers/service_manager.sh"
# shellcheck source=scripts/helpers/runserver_preflight.sh
. "$BASE_DIR/scripts/helpers/runserver_preflight.sh"
SERVICE_STACK_STOPPED=0

log_event() {
  local phase="$1"
  local status="$2"
  local started_at="${3:-}"
  local ended_at="${4:-}"
  local elapsed="${5:-}"
  printf '{"timestamp":"%s","phase":"%s","status":"%s","started_at":"%s","ended_at":"%s","elapsed_seconds":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$phase" "$status" "$started_at" "$ended_at" "${elapsed:-0}"
}

control_unit() {
  local action="$1"
  local unit="$2"

  if [ -z "$unit" ] || ! command -v systemctl >/dev/null 2>&1; then
    return 0
  fi

  if declare -F _arthexis_systemd_unit_present >/dev/null 2>&1 \
    && ! _arthexis_systemd_unit_present "$unit"; then
    return 0
  fi

  local runner=(systemctl)
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    runner=(sudo -n systemctl)
  fi

  "${runner[@]}" "$action" "$unit" || true
}

control_service_stack() {
  local action="$1"
  local service_name="$2"
  local unit

  if [ -z "$service_name" ]; then
    return 0
  fi

  while IFS= read -r unit; do
    control_unit "$action" "$unit"
  done < <(arthexis_service_unit_names "$service_name" true true true true)
}

cleanup_service_stack() {
  local status=$?

  if [ "$SERVICE_STACK_STOPPED" -eq 1 ]; then
    control_service_stack start "$SERVICE_NAME"
    SERVICE_STACK_STOPPED=0
  fi

  exit "$status"
}

run_predeploy_migrations() {
  local python_bin=""
  local fingerprint=""
  local started_at_epoch
  local ended_at_epoch
  local started_at_iso
  local ended_at_iso

  if ! python_bin="$(arthexis_python_bin)"; then
    echo "python3 or python not available" >&2
    return 1
  fi

  mkdir -p "$LOCK_DIR" "$LOG_DIR"

  if ! fingerprint="$(compute_migration_fingerprint "$BASE_DIR")"; then
    echo "Failed to compute migration fingerprint before deploy migration." >&2
    return 1
  fi

  started_at_epoch="$(date +%s)"
  started_at_iso="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  log_event "predeploy_migrate" "start" "$started_at_iso" "" 0

  local target_version=""
  if [ -f "$BASE_DIR/VERSION" ]; then
    target_version="$(tr -d '[:space:]' < "$BASE_DIR/VERSION")"
  fi

  if [ -n "$target_version" ]; then
    (cd "$BASE_DIR" && "$python_bin" manage.py apply_release_migrations "$target_version")
  else
    (cd "$BASE_DIR" && "$python_bin" manage.py migrate --noinput)
  fi
  (cd "$BASE_DIR" && "$python_bin" manage.py migrate --check)

  ended_at_epoch="$(date +%s)"
  ended_at_iso="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  log_event "predeploy_migrate" "success" "$started_at_iso" "$ended_at_iso" "$((ended_at_epoch - started_at_epoch))"

  "$python_bin" - "$MIGRATION_MARKER_FILE" "$fingerprint" "$started_at_iso" "$ended_at_iso" <<'PY'
import json
import pathlib
import sys

marker = pathlib.Path(sys.argv[1])
fingerprint = sys.argv[2]
started_at = sys.argv[3]
ended_at = sys.argv[4]
marker.parent.mkdir(parents=True, exist_ok=True)
marker.write_text(
    json.dumps(
        {
            "status": "success",
            "fingerprint": fingerprint,
            "started_at": started_at,
            "ended_at": ended_at,
        }
    ),
    encoding="utf-8",
)
PY
}

started_at_epoch="$(date +%s)"
started_at_iso="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
log_event "deploy_orchestration" "start" "$started_at_iso" "" 0

log_event "service_switch" "start" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "" 0
trap cleanup_service_stack EXIT
control_service_stack stop "$SERVICE_NAME"
if [ -n "$SERVICE_NAME" ]; then
  SERVICE_STACK_STOPPED=1
fi

run_predeploy_migrations

STATUS=0
if [ -x "${DEPLOY_CMD[0]}" ]; then
  (cd "$BASE_DIR" && "${DEPLOY_CMD[@]}") || STATUS=$?
else
  echo "Deploy command ${DEPLOY_CMD[*]} is not executable" >&2
  STATUS=1
fi

control_service_stack start "$SERVICE_NAME"
SERVICE_STACK_STOPPED=0

ended_at_epoch="$(date +%s)"
ended_at_iso="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ "$STATUS" -eq 0 ]; then
  log_event "deploy_orchestration" "success" "$started_at_iso" "$ended_at_iso" "$((ended_at_epoch - started_at_epoch))"
else
  log_event "deploy_orchestration" "failed" "$started_at_iso" "$ended_at_iso" "$((ended_at_epoch - started_at_epoch))"
fi

exit "$STATUS"
