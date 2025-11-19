#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/ports.sh
. "$BASE_DIR/scripts/helpers/ports.sh"
# shellcheck source=scripts/helpers/service_manager.sh
. "$BASE_DIR/scripts/helpers/service_manager.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"

LOCK_DIR="$BASE_DIR/locks"
SERVICE_MANAGEMENT_MODE="$(arthexis_detect_service_mode "$LOCK_DIR")"

# Use non-interactive sudo if available
SUDO="sudo -n"
if ! $SUDO true 2>/dev/null; then
  SUDO=""
fi

ALL=false
DEFAULT_PORT="$(arthexis_detect_backend_port "$BASE_DIR")"
PORT="$DEFAULT_PORT"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)
      ALL=true
      shift
      ;;
    *)
      PORT="$1"
      shift
      ;;
  esac
done

# Stop systemd-managed services when present
if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE_NAME="$(cat "$LOCK_DIR/service.lck")"
  if systemctl list-unit-files | grep -Fq "${SERVICE_NAME}.service"; then
    $SUDO systemctl stop "$SERVICE_NAME" || true
    $SUDO systemctl status "$SERVICE_NAME" --no-pager || true

    if [ -f "$LOCK_DIR/celery.lck" ]; then
      CELERY_SERVICE="celery-$SERVICE_NAME"
      CELERY_BEAT_SERVICE="celery-beat-$SERVICE_NAME"
      CELERY_UNITS_FOUND=false
      if systemctl list-unit-files | grep -Fq "${CELERY_BEAT_SERVICE}.service"; then
        CELERY_UNITS_FOUND=true
        $SUDO systemctl stop "$CELERY_BEAT_SERVICE" || true
        $SUDO systemctl status "$CELERY_BEAT_SERVICE" --no-pager || true
      fi
      if systemctl list-unit-files | grep -Fq "${CELERY_SERVICE}.service"; then
        CELERY_UNITS_FOUND=true
        $SUDO systemctl stop "$CELERY_SERVICE" || true
        $SUDO systemctl status "$CELERY_SERVICE" --no-pager || true
      fi

      if [ "$CELERY_UNITS_FOUND" = false ]; then
        # Fall back to pkill when Celery services exist but aren't managed via systemd.
        pkill -f "celery -A config" || true
      fi
    fi

    if arthexis_lcd_feature_enabled "$LOCK_DIR"; then
      LCD_SERVICE="lcd-$SERVICE_NAME"
      if systemctl list-unit-files | grep -Fq "${LCD_SERVICE}.service"; then
        $SUDO systemctl stop "$LCD_SERVICE" || true
        $SUDO systemctl status "$LCD_SERVICE" --no-pager || true
      fi
    fi

    exit 0
  fi
fi

# Fall back to stopping locally-run processes
PATTERN="manage.py runserver"
if [ "$ALL" = true ]; then
  pkill -f "$PATTERN" || true
else
  pkill -f "$PATTERN 0.0.0.0:$PORT" || true
fi
# Also stop any Celery components started by start.sh
pkill -f "celery -A config" || true
if arthexis_lcd_feature_enabled "$LOCK_DIR"; then
  if [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_EMBEDDED" ] || ! command -v systemctl >/dev/null 2>&1; then
    pkill -f "python -m core\.lcd_screen" || true
  fi
fi
