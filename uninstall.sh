#!/usr/bin/env bash
set -e

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

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

if [ -z "$SERVICE" ] && [ -f envs/service ]; then
    SERVICE="$(cat envs/service)"
fi

read -p "This will stop the Arthexis server. Continue? [y/N] " CONFIRM
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
    rm -f envs/service
else
    pkill -f "manage.py runserver" || true
fi
