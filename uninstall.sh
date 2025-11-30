#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$SCRIPT_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/systemd_locks.sh
. "$SCRIPT_DIR/scripts/helpers/systemd_locks.sh"
# shellcheck source=scripts/helpers/service_manager.sh
. "$SCRIPT_DIR/scripts/helpers/service_manager.sh"
arthexis_resolve_log_dir "$SCRIPT_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

SERVICE=""
NO_WARN=0

usage() {
    echo "Usage: $0 [--service NAME] [--no-warn]" >&2
    exit 1
}

confirm_database_deletion() {
    local action="$1"
    local -a targets=()

    if [ -f "$BASE_DIR/db.sqlite3" ]; then
        targets+=("db.sqlite3")
    fi

    if [ ${#targets[@]} -eq 0 ] || [ "$NO_WARN" -eq 1 ]; then
        return 0
    fi

    echo "Warning: $action will delete the following database files without creating a backup:"
    local target
    for target in "${targets[@]}"; do
        echo "  - $target"
    done
    echo "Use --no-warn to bypass this prompt."
    local response
    read -r -p "Continue? [y/N] " response
    if [[ ! $response =~ ^[Yy]$ ]]; then
        return 1
    fi

    return 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --service)
            [ -z "$2" ] && usage
            SERVICE="$2"
            shift 2
            ;;
        --no-warn)
            NO_WARN=1
            shift
            ;;
        *)
            usage
            ;;
    esac
done

BASE_DIR="$SCRIPT_DIR"
cd "$BASE_DIR"
LOCK_DIR="$BASE_DIR/.locks"
SYSTEMD_UNITS_LOCK="$LOCK_DIR/systemd_services.lck"
RECORDED_SYSTEMD_UNITS=()
if [ -f "$SYSTEMD_UNITS_LOCK" ]; then
    mapfile -t RECORDED_SYSTEMD_UNITS < "$SYSTEMD_UNITS_LOCK"
fi

if [ -z "$SERVICE" ] && [ -f "$LOCK_DIR/service.lck" ]; then
    SERVICE="$(cat "$LOCK_DIR/service.lck")"
fi
if [ -z "$SERVICE" ] && [ ${#RECORDED_SYSTEMD_UNITS[@]} -gt 0 ]; then
    for unit in "${RECORDED_SYSTEMD_UNITS[@]}"; do
        case "$unit" in
            *-upgrade-guard.service|*-upgrade-guard.timer|celery-*.service|celery-beat-*.service|lcd-*.service)
                continue
                ;;
        esac
        if [[ "$unit" == *.service ]]; then
            SERVICE="${unit%.service}"
            break
        fi
    done
    if [ -z "$SERVICE" ]; then
        for unit in "${RECORDED_SYSTEMD_UNITS[@]}"; do
            if [[ "$unit" == *.service ]]; then
                SERVICE="${unit%.service}"
                break
            fi
        done
    fi
fi

read -r -p "This will stop the Arthexis server. Continue? [y/N] " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Aborted."
    exit 0
fi

if ! confirm_database_deletion "Uninstalling Arthexis"; then
    echo "Uninstall aborted."
    exit 0
fi

if [ -n "$SERVICE" ]; then
    arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${SERVICE}.service"

    GUARD_SERVICE="${SERVICE}-upgrade-guard"
    arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${GUARD_SERVICE}.service"
    arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${GUARD_SERVICE}.timer"

    LCD_SERVICE="lcd-$SERVICE"
    if [ -f "$LOCK_DIR/lcd_screen.lck" ] || printf '%s\n' "${RECORDED_SYSTEMD_UNITS[@]}" | grep -Fxq "${LCD_SERVICE}.service"; then
        arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${LCD_SERVICE}.service"
        rm -f "$LOCK_DIR/lcd_screen.lck"
        arthexis_disable_lcd_feature_flag "$LOCK_DIR"
    fi

    if [ -f "$LOCK_DIR/celery.lck" ]; then
        CELERY_SERVICE="celery-$SERVICE"
        CELERY_BEAT_SERVICE="celery-beat-$SERVICE"
    else
        CELERY_SERVICE=""
        CELERY_BEAT_SERVICE=""
        for unit in "${RECORDED_SYSTEMD_UNITS[@]}"; do
            case "$unit" in
                celery-*.service)
                    CELERY_SERVICE="${unit%.service}"
                    ;;
                celery-beat-*.service)
                    CELERY_BEAT_SERVICE="${unit%.service}"
                    ;;
            esac
        done
    fi

    if [ -n "$CELERY_SERVICE" ]; then
        arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${CELERY_SERVICE}.service"
    fi
    if [ -n "$CELERY_BEAT_SERVICE" ]; then
        arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${CELERY_BEAT_SERVICE}.service"
    fi

    rm -f "$LOCK_DIR/celery.lck"
    rm -f "$LOCK_DIR/service.lck"
    rm -f "$LOCK_DIR/service_mode.lck"
else
    pkill -f "manage.py runserver" || true
fi

if [ ${#RECORDED_SYSTEMD_UNITS[@]} -gt 0 ]; then
    for unit in "${RECORDED_SYSTEMD_UNITS[@]}"; do
        arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "$unit"
    done
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

# Preserve user data fixtures; do not remove user data exported under data/
DATA_DIR="$BASE_DIR/data"
if [ -d "$DATA_DIR" ]; then
    if ls "$DATA_DIR"/*.json >/dev/null 2>&1; then
        echo "Preserving user data fixtures in $DATA_DIR. Remove manually if you want to clear personal fixtures."
    else
        echo "Preserving user data directory at $DATA_DIR (no user data fixtures detected)."
    fi
fi

# Remove the local SQLite database if it exists
DB_FILE="$BASE_DIR/db.sqlite3"
if [ -f "$DB_FILE" ]; then
    rm -f "$DB_FILE"
fi

# Clear lock directory and other cached configuration
rm -rf "$LOCK_DIR"
rm -f "$LOCK_DIR/requirements.md5"

echo "Uninstall complete."
