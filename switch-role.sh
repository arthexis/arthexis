#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$SCRIPT_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/nginx_maintenance.sh
. "$SCRIPT_DIR/scripts/helpers/nginx_maintenance.sh"
# shellcheck source=scripts/helpers/ports.sh
. "$SCRIPT_DIR/scripts/helpers/ports.sh"
arthexis_resolve_log_dir "$SCRIPT_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

SERVICE=""
NODE_ROLE=""
NGINX_MODE="internal"
ENABLE_CELERY=false
ENABLE_LCD_SCREEN=false
ENABLE_CONTROL=false
REQUIRES_REDIS=false
UPDATE=false
CLEAN=false
LATEST=false
CHECK=false
AUTO_UPGRADE_MODE=""
REFRESH_MAINTENANCE=false

BASE_DIR="$SCRIPT_DIR"
LOCK_DIR="$BASE_DIR/locks"
DB_FILE="$BASE_DIR/db.sqlite3"

usage() {
    echo "Usage: $0 [--service NAME] [--update] [--latest] [--clean] [--check] [--auto-upgrade|--no-auto-upgrade] [--refresh-maintenance] [--satellite|--terminal|--control|--watchtower]" >&2
    exit 1
}


detect_service_port() {
    local service_name="$1"
    local nginx_mode="$2"
    local port=""
    local fallback="$(arthexis_detect_backend_port "$SCRIPT_DIR")"

    if [ -n "$service_name" ]; then
        local service_file="/etc/systemd/system/${service_name}.service"
        if [ -f "$service_file" ]; then
            port=$(grep -Eo '0\.0\.0\.0:([0-9]+)' "$service_file" | sed -E 's/.*:([0-9]+)/\1/' | tail -n1)
        fi
    fi

    if [ -z "$port" ]; then
        local nginx_conf="/etc/nginx/sites-enabled/arthexis.conf"
        if [ ! -f "$nginx_conf" ]; then
            nginx_conf="/etc/nginx/conf.d/arthexis-${nginx_mode}.conf"
        fi
        if [ -f "$nginx_conf" ]; then
            port=$(grep -E 'proxy_pass http://127\.0\.0\.1:[0-9]+' "$nginx_conf" | head -n1 | sed -E 's/.*127\.0\.0\.1:([0-9]+).*/\1/')
        fi
    fi

    if [ -z "$port" ]; then
        port="$fallback"
    fi

    echo "$port"
}



require_nginx() {
    if ! command -v nginx >/dev/null 2>&1; then
        echo "Nginx is required for the $1 role but is not installed." >&2
        exit 1
    fi
}

require_redis() {
    if ! command -v redis-cli >/dev/null 2>&1; then
        echo "Redis is required for the $1 role but is not installed." >&2
        echo "Install redis-server and re-run this script. For Debian/Ubuntu:" >&2
        echo "  sudo apt-get update && sudo apt-get install redis-server" >&2
        exit 1
    fi
    if ! redis-cli ping >/dev/null 2>&1; then
        echo "Redis is required for the $1 role but does not appear to be running." >&2
        echo "Start redis and re-run this script. For Debian/Ubuntu:" >&2
        echo "  sudo systemctl start redis-server" >&2
        exit 1
    fi
    cat > "$SCRIPT_DIR/redis.env" <<'EOF_REDIS'
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
EOF_REDIS
}

run_auto_upgrade_management() {
    local action="$1"
    local python_bin=""

    if [ -x "$BASE_DIR/.venv/bin/python" ]; then
        python_bin="$BASE_DIR/.venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        python_bin="$(command -v python3)"
    else
        return
    fi

    if [ "$action" = "enable" ]; then
        "$python_bin" "$BASE_DIR/manage.py" shell <<'PYCODE' || true
from core.auto_upgrade import ensure_auto_upgrade_periodic_task

ensure_auto_upgrade_periodic_task()
PYCODE
    else
        "$python_bin" "$BASE_DIR/manage.py" shell <<'PYCODE' || true
from core.auto_upgrade import AUTO_UPGRADE_TASK_NAME
try:
    from django_celery_beat.models import PeriodicTask
except Exception:
    pass
else:
    PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).delete()
PYCODE
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --service)
            [ -z "$2" ] && usage
            SERVICE="$2"
            shift 2
            ;;
        --update)
            UPDATE=true
            shift
            ;;
        --clean)
            CLEAN=true
            shift
            ;;
        --latest)
            LATEST=true
            shift
            ;;
        --check)
            CHECK=true
            shift
            ;;
        --refresh-maintenance)
            REFRESH_MAINTENANCE=true
            shift
            ;;
        --auto-upgrade)
            if [ "$AUTO_UPGRADE_MODE" = "disable" ]; then
                echo "Cannot combine --auto-upgrade with --no-auto-upgrade" >&2
                usage
            fi
            AUTO_UPGRADE_MODE="enable"
            shift
            ;;
        --no-auto-upgrade)
            if [ "$AUTO_UPGRADE_MODE" = "enable" ]; then
                echo "Cannot combine --auto-upgrade with --no-auto-upgrade" >&2
                usage
            fi
            AUTO_UPGRADE_MODE="disable"
            shift
            ;;
        --satellite)
            require_nginx "satellite"
            NODE_ROLE="Satellite"
            ENABLE_CELERY=true
            NGINX_MODE="internal"
            REQUIRES_REDIS=true
            shift
            ;;
        --terminal)
            NODE_ROLE="Terminal"
            ENABLE_CELERY=true
            NGINX_MODE="internal"
            shift
            ;;
        --control)
            require_nginx "control"
            NODE_ROLE="Control"
            ENABLE_CELERY=true
            ENABLE_LCD_SCREEN=true
            ENABLE_CONTROL=true
            NGINX_MODE="internal"
            REQUIRES_REDIS=true
            shift
            ;;
        --watchtower)
            require_nginx "watchtower"
            NODE_ROLE="Watchtower"
            ENABLE_CELERY=true
            NGINX_MODE="public"
            REQUIRES_REDIS=true
            shift
            ;;
        *)
            usage
            ;;
    esac

done

if [ "$REFRESH_MAINTENANCE" = true ]; then
    if [ -n "$NODE_ROLE" ] || [ -n "$SERVICE" ] || [ "$UPDATE" = true ] || \
       [ "$CLEAN" = true ] || [ "$LATEST" = true ] || [ "$CHECK" = true ] || \
       [ -n "$AUTO_UPGRADE_MODE" ] || \
       [ "$ENABLE_CELERY" = true ] || [ "$ENABLE_LCD_SCREEN" = true ] || \
       [ "$ENABLE_CONTROL" = true ] || [ "$REQUIRES_REDIS" = true ]; then
        echo "--refresh-maintenance cannot be combined with other options" >&2
        usage
    fi
    if arthexis_can_manage_nginx; then
        arthexis_refresh_nginx_maintenance "$BASE_DIR"
    else
        echo "nginx not detected; unable to refresh maintenance assets" >&2
        exit 1
    fi
    exit 0
fi

if [ "$CHECK" = true ]; then
    if [ -f "$LOCK_DIR/role.lck" ]; then
        echo "Role: $(cat "$LOCK_DIR/role.lck")"
    else
        echo "Role: unknown"
    fi

    if [ -f "$LOCK_DIR/auto_upgrade.lck" ]; then
        case "$(cat "$LOCK_DIR/auto_upgrade.lck")" in
            latest)
                echo "Auto-upgrade: enabled (latest channel)"
                ;;
            version)
                echo "Auto-upgrade: enabled (stable channel)"
                ;;
            *)
                echo "Auto-upgrade: enabled (unknown channel)"
                ;;
        esac
    else
        echo "Auto-upgrade: disabled"
    fi

    exit 0
fi

if [ -n "$AUTO_UPGRADE_MODE" ] && [ -z "$NODE_ROLE" ]; then
    mkdir -p "$LOCK_DIR"
    if [ "$AUTO_UPGRADE_MODE" = "enable" ]; then
        if [ "$LATEST" = true ]; then
            echo "latest" > "$LOCK_DIR/auto_upgrade.lck"
        else
            echo "version" > "$LOCK_DIR/auto_upgrade.lck"
        fi
        run_auto_upgrade_management enable
        if [ "$LATEST" = true ]; then
            echo "Auto-upgrade enabled on the latest channel."
        else
            echo "Auto-upgrade enabled on the stable channel."
        fi
    else
        rm -f "$LOCK_DIR/auto_upgrade.lck"
        run_auto_upgrade_management disable
        echo "Auto-upgrade disabled."
    fi
    exit 0
fi

if [ -z "$NODE_ROLE" ]; then
    usage
fi

mkdir -p "$LOCK_DIR"

if [ -z "$SERVICE" ] && [ -f "$LOCK_DIR/service.lck" ]; then
    SERVICE="$(cat "$LOCK_DIR/service.lck")"
fi

if [ "$REQUIRES_REDIS" = true ]; then
    require_redis "$NODE_ROLE"
fi
if [ "$CLEAN" = true ] && [ -f "$DB_FILE" ]; then
    BACKUP_DIR="$BASE_DIR/backups"
    mkdir -p "$BACKUP_DIR"
    VERSION="unknown"
    [ -f "$BASE_DIR/VERSION" ] && VERSION="$(cat "$BASE_DIR/VERSION")"
    REVISION="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
    STAMP="$(date +%Y%m%d%H%M%S)"
    cp "$DB_FILE" "$BACKUP_DIR/db.sqlite3.${VERSION}.${REVISION}.${STAMP}.bak"
    rm "$DB_FILE"
fi

SERVICE_ACTIVE=false
if [ -n "$SERVICE" ] && systemctl list-unit-files | grep -Fq "${SERVICE}.service"; then
    if systemctl is-active --quiet "$SERVICE"; then
        SERVICE_ACTIVE=true
        if ! "$BASE_DIR/stop.sh"; then
            echo "Role change aborted because stop.sh detected active charging sessions. Resolve the sessions or run ./stop.sh --force during a maintenance window." >&2
            exit 1
        fi
    fi
fi

for lock_name in celery.lck lcd_screen.lck control.lck nginx_mode.lck role.lck service.lck; do
    rm -f "$LOCK_DIR/$lock_name"
done
rm -f "$BASE_DIR"/*.role "$BASE_DIR"/.*.role 2>/dev/null || true

if [ "$ENABLE_CELERY" = true ]; then
    touch "$LOCK_DIR/celery.lck"
fi
if [ "$ENABLE_LCD_SCREEN" = true ]; then
    touch "$LOCK_DIR/lcd_screen.lck"
fi
if [ "$ENABLE_CONTROL" = true ]; then
    touch "$LOCK_DIR/control.lck"
fi

echo "$NGINX_MODE" > "$LOCK_DIR/nginx_mode.lck"
echo "$NODE_ROLE" > "$LOCK_DIR/role.lck"
if [ -n "$SERVICE" ]; then
    echo "$SERVICE" > "$LOCK_DIR/service.lck"
fi

if [ "$AUTO_UPGRADE_MODE" = "enable" ]; then
    if [ "$LATEST" = true ]; then
        echo "latest" > "$LOCK_DIR/auto_upgrade.lck"
    else
        echo "version" > "$LOCK_DIR/auto_upgrade.lck"
    fi
    run_auto_upgrade_management enable
    if [ "$LATEST" = true ]; then
        echo "Auto-upgrade enabled on the latest channel."
    else
        echo "Auto-upgrade enabled on the stable channel."
    fi
elif [ "$AUTO_UPGRADE_MODE" = "disable" ]; then
    rm -f "$LOCK_DIR/auto_upgrade.lck"
    run_auto_upgrade_management disable
    echo "Auto-upgrade disabled."
fi

if arthexis_can_manage_nginx; then
    arthexis_refresh_nginx_maintenance "$BASE_DIR" "/etc/nginx/sites-enabled/arthexis.conf"
fi

if [ "$UPDATE" = true ]; then
    if [ "$LATEST" = true ]; then
        "$BASE_DIR/upgrade.sh" --latest
    else
        "$BASE_DIR/upgrade.sh"
    fi
fi

if [ "$SERVICE_ACTIVE" = true ]; then
    sudo systemctl start "$SERVICE"
    if [ "$ENABLE_CELERY" = true ]; then
        sudo systemctl start "celery-$SERVICE" || true
        sudo systemctl start "celery-beat-$SERVICE" || true
    fi
    if [ "$ENABLE_LCD_SCREEN" = true ]; then
        sudo systemctl start "lcd-$SERVICE" || true
    fi
fi

