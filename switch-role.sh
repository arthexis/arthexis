#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$SCRIPT_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/nginx_maintenance.sh
. "$SCRIPT_DIR/scripts/helpers/nginx_maintenance.sh"
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
REFRESH_MAINTENANCE=false

BASE_DIR="$SCRIPT_DIR"
LOCK_DIR="$BASE_DIR/locks"
DB_FILE="$BASE_DIR/db.sqlite3"

usage() {
    echo "Usage: $0 [--service NAME] [--update] [--latest] [--clean] [--datasette|--no-datasette] [--check] [--auto-upgrade|--no-auto-upgrade] [--refresh-maintenance] [--satellite|--terminal|--control|--watchtower]" >&2
    exit 1
}

ensure_datasette_package() {
    if [ -x "$BASE_DIR/.venv/bin/pip" ]; then
        "$BASE_DIR/.venv/bin/pip" install datasette
    elif command -v pip3 >/dev/null 2>&1; then
        pip3 install datasette
    fi
}

detect_service_port() {
    local service_name="$1"
    local nginx_mode="$2"
    local port=""

    if [ -n "$service_name" ]; then
        local service_file="/etc/systemd/system/${service_name}.service"
        if [ -f "$service_file" ]; then
            port=$(grep -Eo '0\.0\.0\.0:([0-9]+)' "$service_file" | sed -E 's/.*:([0-9]+)/\1/' | tail -n1)
        fi
    fi

    if [ -z "$port" ]; then
        local nginx_conf="/etc/nginx/conf.d/arthexis-${nginx_mode}.conf"
        if [ -f "$nginx_conf" ]; then
            port=$(grep -E 'proxy_pass http://127\.0\.0\.1:[0-9]+' "$nginx_conf" | head -n1 | sed -E 's/.*127\.0\.0\.1:([0-9]+).*/\1/')
        fi
    fi

    if [ -z "$port" ]; then
        if [ "$nginx_mode" = "public" ]; then
            port=8000
        else
            port=8888
        fi
    fi

    echo "$port"
}

update_nginx_for_datasette() {
    local nginx_conf="$1"
    local datasette_port="$2"
    local action="$3"

    if [ ! -f "$nginx_conf" ]; then
        return 0
    fi

    if [ "$action" = "enable" ]; then
        sudo python3 - "$nginx_conf" "$datasette_port" <<'PYCODE'
import sys
import textwrap
from pathlib import Path

nginx_conf = Path(sys.argv[1])
datasette_port = sys.argv[2]
content = nginx_conf.read_text()

block = textwrap.dedent(
    f"""
    location /data/ {{
        auth_request /datasette-auth/;
        error_page 401 =302 /login/?next=$request_uri;
        proxy_pass http://127.0.0.1:{datasette_port}/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
"""
).strip("\n")

indented_block = textwrap.indent(block, "    ")

import re

pattern = re.compile(r"\n\s*location /data/\s*\{.*?\n\s*\}", re.DOTALL)

def update_port(match):
    section = match.group(0)
    return re.sub(
        r"(proxy_pass http://127\.0\.0\.1:)([0-9]+)(/;)",
        lambda m: f"{m.group(1)}{datasette_port}{m.group(3)}",
        section,
    )

new_content, count = pattern.subn(update_port, content, count=1)

if count:
    content = new_content
else:
    marker = "location /mcp/ {"
    marker_index = content.find(marker)
    if marker_index != -1:
        close_index = content.find("}", marker_index)
        if close_index != -1:
            newline_index = content.find("\n", close_index)
            if newline_index == -1:
                newline_index = close_index + 1
            insertion_point = newline_index
            content = content[:insertion_point] + "\n" + indented_block + "\n" + content[insertion_point:]
        else:
            content = content.rstrip() + "\n" + indented_block + "\n"
    else:
        content = content.rstrip() + "\n" + indented_block + "\n"

if not content.endswith("\n"):
    content += "\n"

nginx_conf.write_text(content)
PYCODE
    else
        sudo python3 - "$nginx_conf" <<'PYCODE'
import sys
import re
from pathlib import Path

nginx_conf = Path(sys.argv[1])
content = nginx_conf.read_text()

pattern = re.compile(r"\n\s*location /data/\s*\{.*?\n\s*\}", re.DOTALL)
content, _ = pattern.subn("\n", content)

content = content.rstrip() + "\n"
nginx_conf.write_text(content)
PYCODE
    fi

    if command -v nginx >/dev/null 2>&1; then
        sudo nginx -t
        sudo systemctl reload nginx || echo "Warning: nginx reload failed"
    fi
}

ensure_datasette_service() {
    local service_name="$1"
    local nginx_mode="$2"
    local main_port="$3"

    local nginx_conf="/etc/nginx/conf.d/arthexis-${nginx_mode}.conf"
    local datasette_port=$((main_port + 1))

    ensure_datasette_package
    update_nginx_for_datasette "$nginx_conf" "$datasette_port" enable

    if [ -n "$service_name" ]; then
        local datasette_service="datasette-$service_name"
        local service_file="/etc/systemd/system/${datasette_service}.service"
        sudo bash -c "cat > '$service_file'" <<SERVICEEOF
[Unit]
Description=Datasette for $service_name
After=network.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
ExecStart=$BASE_DIR/.venv/bin/datasette serve $DB_FILE --host 127.0.0.1 --port $datasette_port --setting base_url /data/
Restart=always
User=$(id -un)

[Install]
WantedBy=multi-user.target
SERVICEEOF
        sudo systemctl daemon-reload
        sudo systemctl enable "$datasette_service" || true
    fi
}

disable_datasette_service() {
    local service_name="$1"
    local nginx_mode="$2"

    if [ -n "$service_name" ]; then
        local datasette_service="datasette-$service_name"
        if systemctl list-unit-files | grep -Fq "${datasette_service}.service"; then
            sudo systemctl stop "$datasette_service" || true
            sudo systemctl disable "$datasette_service" || true
            local service_file="/etc/systemd/system/${datasette_service}.service"
            if [ -f "$service_file" ]; then
                sudo rm "$service_file"
            fi
            sudo systemctl daemon-reload
        fi
    fi

    local nginx_conf="/etc/nginx/conf.d/arthexis-${nginx_mode}.conf"
    update_nginx_for_datasette "$nginx_conf" "" disable
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
        --constellation)
            echo "The Constellation role has been renamed to Watchtower." >&2
            echo "Use --watchtower for future invocations." >&2
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
       [ -n "$AUTO_UPGRADE_MODE" ] || [ "$ENABLE_DATASETTE" = false ] || \
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
    MAIN_SERVICE_PORT=$(detect_service_port "$SERVICE" "$NGINX_MODE")
    ensure_datasette_service "$SERVICE" "$NGINX_MODE" "$MAIN_SERVICE_PORT"
else
    disable_datasette_service "$SERVICE" "$NGINX_MODE"
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

if arthexis_can_manage_nginx; then
    arthexis_refresh_nginx_maintenance "$BASE_DIR" "/etc/nginx/conf.d/arthexis-${NGINX_MODE}.conf"
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

