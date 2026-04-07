#!/usr/bin/env bash
set -u

BASE_DIR=""
SERVICE_NAME=""
BACKOFF_SECONDS="${ARTHEXIS_BOOT_UPGRADE_BACKOFF_SECONDS:-1800}"
CHECK_TTL_SECONDS="${ARTHEXIS_BOOT_UPGRADE_CHECK_TTL_SECONDS:-300}"
FORCE_CHECK="${ARTHEXIS_BOOT_UPGRADE_FORCE_CHECK:-0}"
LOCK_SUBDIR=".locks"
BACKOFF_LOCK=""
RECENCY_LOCK=""
FORCE_CHECK_ENABLED=0

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
RECENCY_LOCK="$BASE_DIR/$LOCK_SUBDIR/${SERVICE_NAME}-boot-upgrade-last-check.lck"
mkdir -p "$BASE_DIR/$LOCK_SUBDIR"

now_epoch="$(date +%s)"
if ! [[ "$CHECK_TTL_SECONDS" =~ ^[0-9]+$ ]]; then
  CHECK_TTL_SECONDS=300
fi
case "${FORCE_CHECK,,}" in
  1|true|yes|on)
    FORCE_CHECK_ENABLED=1
    ;;
esac
if [ -f "$BACKOFF_LOCK" ]; then
  backoff_until="$(tr -d '\r\n\t ' < "$BACKOFF_LOCK")"
  if [[ "$backoff_until" =~ ^[0-9]+$ ]] && [ "$now_epoch" -lt "$backoff_until" ]; then
    echo "Boot upgrade skipped for ${SERVICE_NAME}; backoff active until $(date -d "@$backoff_until" -u +'%Y-%m-%dT%H:%M:%SZ')."
    exit 0
  fi
fi

resolve_local_revision() {
  local revision

  revision="$(
    cd "$BASE_DIR" 2>/dev/null \
      && git rev-parse HEAD 2>/dev/null
  )" || return 1

  if [[ "$revision" =~ ^[0-9a-fA-F]{40}$ ]]; then
    printf '%s\n' "$revision"
    return 0
  fi

  return 1
}

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

local_revision="$(resolve_local_revision)" || local_revision=""

if [ "$FORCE_CHECK_ENABLED" -ne 1 ] && [ "$CHECK_TTL_SECONDS" -gt 0 ] && [ -n "$local_revision" ] && [ -f "$RECENCY_LOCK" ]; then
  recency_epoch=""
  recency_revision=""
  if IFS='|' read -r recency_epoch recency_revision < "$RECENCY_LOCK"; then
    if [[ "$recency_epoch" =~ ^[0-9]+$ ]] && [ -n "$recency_revision" ]; then
      recency_deadline=$((recency_epoch + CHECK_TTL_SECONDS))
      if [ "$now_epoch" -lt "$recency_deadline" ] && [ "$local_revision" = "$recency_revision" ]; then
        echo "Boot upgrade skipped for ${SERVICE_NAME}; recent successful no-op check still valid (TTL ${CHECK_TTL_SECONDS}s)."
        exit 0
      fi
    fi
  fi
fi

echo "Attempting boot upgrade for ${SERVICE_NAME} (${channel_flag#--} channel)."
if "$BASE_DIR/upgrade.sh" "$channel_flag" --no-start --no-warn; then
  rm -f "$BACKOFF_LOCK"
  local_revision="$(resolve_local_revision)" || local_revision=""
  if [ -n "$local_revision" ]; then
    printf '%s|%s\n' "$now_epoch" "$local_revision" > "$RECENCY_LOCK"
  fi
  echo "Boot upgrade completed for ${SERVICE_NAME}."
  exit 0
fi

backoff_until=$((now_epoch + BACKOFF_SECONDS))
rm -f "$RECENCY_LOCK"
printf '%s\n' "$backoff_until" > "$BACKOFF_LOCK"
echo "Boot upgrade failed for ${SERVICE_NAME}; backing off until $(date -d "@$backoff_until" -u +'%Y-%m-%dT%H:%M:%SZ')."
exit 0
