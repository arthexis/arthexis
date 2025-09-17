#!/bin/bash
set -euo pipefail
BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/wifi-watchdog.log"
LOCK_FILE="$BASE_DIR/locks/charging.lck"
FAILS=0
while true; do
    if ping -c1 -W2 8.8.8.8 >/dev/null 2>&1; then
        echo "$(date -Iseconds) OK" >> "$LOG_FILE"
        FAILS=0
    else
        echo "$(date -Iseconds) FAIL" >> "$LOG_FILE"
        FAILS=$((FAILS+1))
        if [[ $FAILS -ge 3 ]]; then
            AGE=1000
            if [[ -f "$LOCK_FILE" ]]; then
                AGE=$(( $(date +%s) - $(stat -c %Y "$LOCK_FILE") ))
            fi
            if [[ ! -f "$LOCK_FILE" || $AGE -gt 600 ]]; then
                FAILS=0
                /sbin/reboot || true
            else
                FAILS=0
            fi
        fi
    fi
    sleep 300
done
