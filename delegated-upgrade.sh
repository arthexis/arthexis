#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK_DIR="$BASE_DIR/.locks"
SERVICE_NAME=""
LOG_DIR="${ARTHEXIS_LOG_DIR:-$BASE_DIR/logs}"

resolve_run_user() {
  local owner=""

  if stat -c '%U' "$BASE_DIR" >/dev/null 2>&1; then
    owner="$(stat -c '%U' "$BASE_DIR")"
  fi

  if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ] && { [ -z "$owner" ] || [ "$owner" = "root" ]; }; then
    owner="$SUDO_USER"
  fi

  if [ -z "$owner" ] || [ "$owner" = "root" ]; then
    owner="$(id -un)"
  fi

  printf '%s\n' "$owner"
}

resolve_home_dir() {
  local user="$1"
  local home=""

  if [ -n "$user" ] && command -v getent >/dev/null 2>&1; then
    home="$(getent passwd "$user" | awk -F: 'NR==1 {print $6}')"
  fi

  if [ -z "$home" ] && [ -n "$user" ]; then
    home="$(eval echo "~$user" 2>/dev/null || true)"
  fi

  printf '%s\n' "$home"
}

if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE_NAME="$(tr -d '\r\n' < "$LOCK_DIR/service.lck")"
fi

RUN_USER="$(resolve_run_user)"
RUN_HOME="$(resolve_home_dir "$RUN_USER")"

mkdir -p "$LOG_DIR"

DEFAULT_UPGRADE=("$BASE_DIR/upgrade.sh" "--stable")
if [ "$#" -gt 0 ]; then
  UPGRADE_CMD=("$@")
else
  UPGRADE_CMD=("${DEFAULT_UPGRADE[@]}")
fi

SYSTEMD_RUN=$(command -v systemd-run || true)
if [ -z "$SYSTEMD_RUN" ]; then
  echo "systemd-run is required for delegated upgrades" >&2
  exit 1
fi

RUNNER=("$SYSTEMD_RUN")
if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
  RUNNER=(sudo -n "$SYSTEMD_RUN")
fi

WATCH_HELPER="/usr/local/bin/watch-upgrade"
if [ ! -x "$WATCH_HELPER" ]; then
  echo "watch-upgrade helper missing at $WATCH_HELPER" >&2
  exit 1
fi
LOG_FILE="${LOG_DIR}/delegated-upgrade.log"
UNIT_NAME="delegated-upgrade-$(date +%s)"
DELEGATED_CMD=(
  "${RUNNER[@]}"
  --unit "$UNIT_NAME"
  --description "Delegated Arthexis upgrade"
  --property "WorkingDirectory=$BASE_DIR"
  --property "StandardOutput=append:$LOG_FILE"
  --property "StandardError=append:$LOG_FILE"
)

if [ -n "$RUN_USER" ]; then
  DELEGATED_CMD+=(--uid "$RUN_USER" --property "User=$RUN_USER")
  if [ -n "$RUN_HOME" ]; then
    DELEGATED_CMD+=(--setenv "HOME=$RUN_HOME")
  fi
fi

DELEGATED_CMD+=(
  --setenv "ARTHEXIS_BASE_DIR=$BASE_DIR"
  --setenv "ARTHEXIS_LOG_DIR=$LOG_DIR"
  "$WATCH_HELPER"
)

if [ -n "$SERVICE_NAME" ]; then
  DELEGATED_CMD+=("$SERVICE_NAME")
fi

DELEGATED_CMD+=("${UPGRADE_CMD[@]}")

"${DELEGATED_CMD[@]}"

SYSTEMCTL_CMD=()
if command -v systemctl >/dev/null 2>&1; then
  SYSTEMCTL_CMD=(systemctl)
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    SYSTEMCTL_CMD=(sudo -n systemctl)
  fi
fi

if [ ${#SYSTEMCTL_CMD[@]} -gt 0 ]; then
  for attempt in {1..5}; do
    if "${SYSTEMCTL_CMD[@]}" is-active --quiet "${UNIT_NAME}.service"; then
      break
    fi
    sleep 1
  done

  if ! "${SYSTEMCTL_CMD[@]}" is-active --quiet "${UNIT_NAME}.service"; then
    echo "Delegated upgrade unit ${UNIT_NAME} did not start" >&2
    exit 1
  fi
fi

echo "UNIT_NAME=${UNIT_NAME}"

if [ ${#SYSTEMCTL_CMD[@]} -gt 0 ] && [ -n "$SERVICE_NAME" ]; then
  "${SYSTEMCTL_CMD[@]}" stop "$SERVICE_NAME" || true
fi
