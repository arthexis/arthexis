#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK_DIR="$BASE_DIR/locks"
SERVICE_NAME=""
if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE_NAME="$(tr -d '\r\n' < "$LOCK_DIR/service.lck")"
fi

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

UNIT_NAME="delegated-upgrade-$(date +%s)"
DELEGATED_CMD=(
  "${RUNNER[@]}"
  --unit "$UNIT_NAME"
  --description "Delegated Arthexis upgrade"
  --setenv "ARTHEXIS_BASE_DIR=$BASE_DIR"
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
