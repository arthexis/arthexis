#!/usr/bin/env bash
set -u

BASE_DIR=""
SERVICE_NAME=""
BACKOFF_SECONDS="${ARTHEXIS_BOOT_UPGRADE_BACKOFF_SECONDS:-1800}"
LOCK_SUBDIR=".locks"
BACKOFF_LOCK=""

usage() {
  echo "Usage: $0 --base-dir PATH --service NAME" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-dir)
      BASE_DIR="${2:-}"
      shift 2
      ;;
    --service)
      SERVICE_NAME="${2:-}"
      shift 2
      ;;
    *)
      usage
      exit 0
      ;;
  esac
done

if [ -z "$BASE_DIR" ] || [ -z "$SERVICE_NAME" ]; then
  usage
  exit 0
fi

BACKOFF_LOCK="$BASE_DIR/$LOCK_SUBDIR/${SERVICE_NAME}-boot-upgrade-backoff-until.lck"
mkdir -p "$BASE_DIR/$LOCK_SUBDIR"

now_epoch="$(date +%s)"
if [ -f "$BACKOFF_LOCK" ]; then
  backoff_until="$(tr -d '\r\n\t ' < "$BACKOFF_LOCK")"
  if [[ "$backoff_until" =~ ^[0-9]+$ ]] && [ "$now_epoch" -lt "$backoff_until" ]; then
    echo "Boot upgrade skipped for ${SERVICE_NAME}; backoff active until $(date -d "@$backoff_until" -u +'%Y-%m-%dT%H:%M:%SZ')."
    exit 0
  fi
fi

if [ ! -x "$BASE_DIR/upgrade.sh" ]; then
  echo "Boot upgrade skipped for ${SERVICE_NAME}; upgrade.sh is unavailable."
  exit 0
fi

channel_flag="--stable"
if [ -f "$BASE_DIR/$LOCK_SUBDIR/auto_upgrade.lck" ]; then
  configured_channel="$(tr -d '\r\n\t ' < "$BASE_DIR/$LOCK_SUBDIR/auto_upgrade.lck" | tr '[:upper:]' '[:lower:]')"
  if [ "$configured_channel" = "latest" ] || [ "$configured_channel" = "unstable" ]; then
    channel_flag="--latest"
  fi
fi

echo "Attempting boot upgrade for ${SERVICE_NAME} (${channel_flag#--} channel)."
if "$BASE_DIR/upgrade.sh" "$channel_flag" --no-start --no-warn; then
  rm -f "$BACKOFF_LOCK"
  echo "Boot upgrade completed for ${SERVICE_NAME}."
  exit 0
fi

backoff_until=$((now_epoch + BACKOFF_SECONDS))
printf '%s\n' "$backoff_until" > "$BACKOFF_LOCK"
echo "Boot upgrade failed for ${SERVICE_NAME}; backing off until $(date -d "@$backoff_until" -u +'%Y-%m-%dT%H:%M:%SZ')."
exit 0
