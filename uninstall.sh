#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

SERVICE=""

usage() {
    echo "Usage: $0 [--service NAME]" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --service)
            [ -z "$2" ] && usage
            SERVICE="$2"
            shift 2
            ;;
        *)
            usage
            ;;
    esac
done

BASE_DIR="$SCRIPT_DIR"
cd "$BASE_DIR"
LOCK_DIR="$BASE_DIR/locks"

if [ -z "$SERVICE" ] && [ -f "$LOCK_DIR/service.lck" ]; then
    SERVICE="$(cat "$LOCK_DIR/service.lck")"
fi

read -r -p "This will stop the Arthexis server. Continue? [y/N] " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Aborted."
    exit 0
fi

if [ -n "$SERVICE" ] && systemctl list-unit-files | grep -Fq "${SERVICE}.service"; then
    sudo systemctl stop "$SERVICE" || true
    sudo systemctl disable "$SERVICE" || true
    SERVICE_FILE="/etc/systemd/system/${SERVICE}.service"
    if [ -f "$SERVICE_FILE" ]; then
        sudo rm "$SERVICE_FILE"
        sudo systemctl daemon-reload
    fi
    if [ -f "$LOCK_DIR/lcd_screen.lck" ]; then
        LCD_SERVICE="lcd-$SERVICE"
        if systemctl list-unit-files | grep -Fq "${LCD_SERVICE}.service"; then
            sudo systemctl stop "$LCD_SERVICE" || true
            sudo systemctl disable "$LCD_SERVICE" || true
            LCD_SERVICE_FILE="/etc/systemd/system/${LCD_SERVICE}.service"
            if [ -f "$LCD_SERVICE_FILE" ]; then
                sudo rm "$LCD_SERVICE_FILE"
            fi
        fi
        rm -f "$LOCK_DIR/lcd_screen.lck"
    fi
    if [ -f "$LOCK_DIR/celery.lck" ]; then
        CELERY_SERVICE="celery-$SERVICE"
        CELERY_SERVICE_FILE="/etc/systemd/system/${CELERY_SERVICE}.service"
        if systemctl list-unit-files | grep -Fq "${CELERY_SERVICE}.service"; then
            sudo systemctl stop "$CELERY_SERVICE" || true
            sudo systemctl disable "$CELERY_SERVICE" || true
            if [ -f "$CELERY_SERVICE_FILE" ]; then
                sudo rm "$CELERY_SERVICE_FILE"
            fi
        fi
        CELERY_BEAT_SERVICE="celery-beat-$SERVICE"
        CELERY_BEAT_SERVICE_FILE="/etc/systemd/system/${CELERY_BEAT_SERVICE}.service"
        if systemctl list-unit-files | grep -Fq "${CELERY_BEAT_SERVICE}.service"; then
            sudo systemctl stop "$CELERY_BEAT_SERVICE" || true
            sudo systemctl disable "$CELERY_BEAT_SERVICE" || true
            if [ -f "$CELERY_BEAT_SERVICE_FILE" ]; then
                sudo rm "$CELERY_BEAT_SERVICE_FILE"
            fi
        fi
        rm -f "$LOCK_DIR/celery.lck"
    fi
    if [ -f "$LOCK_DIR/datasette.lck" ]; then
        DATASETTE_SERVICE="datasette-$SERVICE"
        if systemctl list-unit-files | grep -Fq "${DATASETTE_SERVICE}.service"; then
            sudo systemctl stop "$DATASETTE_SERVICE" || true
            sudo systemctl disable "$DATASETTE_SERVICE" || true
            DATASETTE_SERVICE_FILE="/etc/systemd/system/${DATASETTE_SERVICE}.service"
            if [ -f "$DATASETTE_SERVICE_FILE" ]; then
                sudo rm "$DATASETTE_SERVICE_FILE"
            fi
        fi
        rm -f "$LOCK_DIR/datasette.lck"
    fi
    rm -f "$LOCK_DIR/service.lck"
else
    pkill -f "manage.py runserver" || true
fi

# Remove wlan1 refresh service if present (legacy and current names)
for svc in wlan1-refresh wlan1-device-refresh; do
    if systemctl list-unit-files | grep -Fq "${svc}.service"; then
        sudo systemctl stop "$svc" || true
        sudo systemctl disable "$svc" || true
        if [ -f "/etc/systemd/system/${svc}.service" ]; then
            sudo rm "/etc/systemd/system/${svc}.service"
            sudo systemctl daemon-reload
        fi
    fi
done

# Ensure any Celery workers or beats are also stopped
pkill -f "celery -A config" || true

# Remove the local SQLite database if it exists
DB_FILE="$BASE_DIR/db.sqlite3"
if [ -f "$DB_FILE" ]; then
    rm -f "$DB_FILE"
fi

# Clear lock directory and other cached configuration
rm -rf "$LOCK_DIR"
rm -f "$BASE_DIR/AUTO_UPGRADE"
rm -f "$BASE_DIR/requirements.md5"

echo "Uninstall complete."
