#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK_DIR="$BASE_DIR/locks"
SKIP_LOCK="$LOCK_DIR/service-start-skip.lck"
mkdir -p "$LOCK_DIR"

echo "Manual start requested; creating one-time skip lock for upgrade checks." \
  >>"$BASE_DIR/logs/start.log" 2>/dev/null || true
# Create a short-lived lock so the upcoming start skips upgrade once.
date +%s > "$SKIP_LOCK"

SYSTEMCTL_CMD=()
if command -v systemctl >/dev/null 2>&1; then
  SYSTEMCTL_CMD=(systemctl)
  if command -v sudo >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null; then
      SYSTEMCTL_CMD=(sudo -n systemctl)
    elif [ "$(id -u)" -ne 0 ]; then
      SYSTEMCTL_CMD=(systemctl)
    fi
  fi
fi

SERVICE_NAME=""
if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE_NAME="$(cat "$LOCK_DIR/service.lck")"
fi

if [ -n "$SERVICE_NAME" ] && [ ${#SYSTEMCTL_CMD[@]} -gt 0 ] \
  && "${SYSTEMCTL_CMD[@]}" list-unit-files | grep -Fq "${SERVICE_NAME}.service"; then
  "${SYSTEMCTL_CMD[@]}" restart "$SERVICE_NAME"
  exit 0
fi

exec "$BASE_DIR/service-start.sh" "$@"
