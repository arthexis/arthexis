#!/usr/bin/env bash
set -euo pipefail

if (( EUID != 0 )); then
    echo "This script must be run as root." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
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

nmcli -t -f NAME,DEVICE,TYPE connection show |
awk -F':' '$3=="wifi" && $2=="wlan1" {print $1}' |
while IFS= read -r con; do
    echo "Updating connection $con to use MAC $MAC"
    nmcli connection modify "$con" 802-11-wireless.mac-address "$MAC"
done

nmcli device reapply wlan1 >/dev/null
