#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

if ! command -v nmcli >/dev/null 2>&1; then
    echo "nmcli not found; skipping wlan1 refresh." >&2
    exit 0
fi

if ! nmcli device show wlan1 >/dev/null 2>&1; then
    echo "wlan1 device not present; nothing to refresh." >&2
    exit 0
fi

MAC="$(nmcli -g GENERAL.HWADDR device show wlan1 2>/dev/null | tr -d '\n')"
if [[ -z "$MAC" ]]; then
    echo "Unable to determine wlan1 MAC address." >&2
    exit 0
fi

while read -r con; do
    echo "Updating connection $con to use MAC $MAC"
    nmcli connection modify "$con" 802-11-wireless.mac-address "$MAC" || true
  done < <(
    nmcli -t -f NAME,TYPE connection show |
    awk -F: '$2=="wifi" {print $1}' |
    while read -r c; do
        if [[ "$(nmcli -g connection.interface-name connection show "$c" 2>/dev/null)" == "wlan1" ]]; then
            echo "$c"
        fi
    done
)

nmcli device reapply wlan1 >/dev/null 2>&1 || true
