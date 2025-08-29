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
    LCD_SERVICE="lcd-$SERVICE"
    if systemctl list-unit-files | grep -Fq "${LCD_SERVICE}.service"; then
        sudo systemctl stop "$LCD_SERVICE" || true
        sudo systemctl disable "$LCD_SERVICE" || true
        LCD_SERVICE_FILE="/etc/systemd/system/${LCD_SERVICE}.service"
        if [ -f "$LCD_SERVICE_FILE" ]; then
            sudo rm "$LCD_SERVICE_FILE"
        fi
    fi
    rm -f "$LOCK_DIR/service.lck"
    rm -f "$LOCK_DIR/lcd_screen.lck"
else
    pkill -f "manage.py runserver" || true
fi
