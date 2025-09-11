#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
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
ENABLE_DATASETTE=false
CHECK=false

usage() {
    echo "Usage: $0 [--service NAME] [--update] [--latest] [--clean] [--datasette] [--check] [--satellite|--terminal|--control|--constellation]" >&2
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
        exit 1
    fi
    if ! redis-cli ping >/dev/null 2>&1; then
        echo "Redis is required for the $1 role but does not appear to be running." >&2
        exit 1
    fi
    cat > "$SCRIPT_DIR/redis.env" <<'EOF_REDIS'
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
EOF_REDIS
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
        --check)
            CHECK=true
            shift
            ;;
        --satellite)
            require_nginx "satellite"
            NODE_ROLE="Gateway"
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
    LOCK_DIR="$SCRIPT_DIR/locks"
    if [ -f "$LOCK_DIR/role.lck" ]; then
        cat "$LOCK_DIR/role.lck"
    else
        echo "unknown"
    fi
    exit 0
fi

if [ -z "$NODE_ROLE" ]; then
    usage
fi

BASE_DIR="$SCRIPT_DIR"
LOCK_DIR="$BASE_DIR/locks"
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

rm -f "$LOCK_DIR"/*.lck
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

