#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$SCRIPT_DIR/scripts/helpers/logging.sh"
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
ENABLE_DATASETTE=true
CHECK=false
AUTO_UPGRADE_MODE=""

BASE_DIR="$SCRIPT_DIR"
LOCK_DIR="$BASE_DIR/locks"

usage() {
    echo "Usage: $0 [--service NAME] [--update] [--latest] [--clean] [--datasette|--no-datasette] [--check] [--auto-upgrade|--no-auto-upgrade] [--satellite|--terminal|--control|--constellation]" >&2
    exit 1
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
        --datasette)
            ENABLE_DATASETTE=true
            shift
            ;;
        --no-datasette)
            ENABLE_DATASETTE=false
            shift
            ;;
        --check)
            CHECK=true
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
        --constellation)
            require_nginx "constellation"
            NODE_ROLE="Constellation"
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

if [ "$CHECK" = true ]; then
    if [ -f "$LOCK_DIR/role.lck" ]; then
        cat "$LOCK_DIR/role.lck"
    else
        echo "unknown"
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
    else
        rm -f "$LOCK_DIR/auto_upgrade.lck"
        run_auto_upgrade_management disable
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

DB_FILE="$BASE_DIR/db.sqlite3"
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
        sudo systemctl stop "$SERVICE"
        if [ -f "$LOCK_DIR/celery.lck" ]; then
            sudo systemctl stop "celery-$SERVICE" || true
            sudo systemctl stop "celery-beat-$SERVICE" || true
        fi
        if [ -f "$LOCK_DIR/lcd_screen.lck" ]; then
            sudo systemctl stop "lcd-$SERVICE" || true
        fi
        if [ -f "$LOCK_DIR/datasette.lck" ]; then
            sudo systemctl stop "datasette-$SERVICE" || true
        fi
    fi
fi

for lock_name in celery.lck lcd_screen.lck control.lck datasette.lck nginx_mode.lck role.lck service.lck; do
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
if [ "$ENABLE_DATASETTE" = true ]; then
    touch "$LOCK_DIR/datasette.lck"
else
    if [ -n "$SERVICE" ]; then
        DATASETTE_SERVICE="datasette-$SERVICE"
        if systemctl list-unit-files | grep -Fq "${DATASETTE_SERVICE}.service"; then
            sudo systemctl stop "$DATASETTE_SERVICE" || true
            sudo systemctl disable "$DATASETTE_SERVICE" || true
            DATASETTE_SERVICE_FILE="/etc/systemd/system/${DATASETTE_SERVICE}.service"
            if [ -f "$DATASETTE_SERVICE_FILE" ]; then
                sudo rm "$DATASETTE_SERVICE_FILE"
            fi
            sudo systemctl daemon-reload
        fi
    fi
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
elif [ "$AUTO_UPGRADE_MODE" = "disable" ]; then
    rm -f "$LOCK_DIR/auto_upgrade.lck"
    run_auto_upgrade_management disable
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
    if [ "$ENABLE_DATASETTE" = true ]; then
        sudo systemctl start "datasette-$SERVICE" || true
    fi
fi

