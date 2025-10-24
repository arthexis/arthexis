#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIP_INSTALL_HELPER="$SCRIPT_DIR/scripts/helpers/pip_install.py"
# shellcheck source=scripts/helpers/logging.sh
. "$SCRIPT_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/nginx_maintenance.sh
. "$SCRIPT_DIR/scripts/helpers/nginx_maintenance.sh"
arthexis_resolve_log_dir "$SCRIPT_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

SERVICE=""
NGINX_MODE="internal"
PORT=""
AUTO_UPGRADE=false
LATEST=false
STABLE=false
UPGRADE=false
ENABLE_CELERY=false
ENABLE_LCD_SCREEN=false
DISABLE_LCD_SCREEN=false
CLEAN=false
ENABLE_CONTROL=false
NODE_ROLE="Terminal"
REQUIRES_REDIS=false
ENABLE_DATASETTE=true
START_SERVICES=false

usage() {
    echo "Usage: $0 [--service NAME] [--public|--internal] [--port PORT] [--upgrade] [--auto-upgrade] [--latest|--stable] [--satellite] [--terminal] [--control] [--watchtower] [--celery] [--lcd-screen|--no-lcd-screen] [--datasette|--no-datasette] [--clean] [--start]" >&2
    exit 1
}

ensure_nginx_in_path() {
    if command -v nginx >/dev/null 2>&1; then
        return 0
    fi

    local -a extra_paths=("/usr/sbin" "/usr/local/sbin" "/sbin")
    local dir
    for dir in "${extra_paths[@]}"; do
        if [ -x "$dir/nginx" ]; then
            case ":$PATH:" in
                *":$dir:"*) ;;
                *) PATH="${PATH:+$PATH:}$dir"
                   export PATH ;;
            esac
            if command -v nginx >/dev/null 2>&1; then
                return 0
            fi
        fi
    done

    return 1
}

require_nginx() {
    if ! ensure_nginx_in_path; then
        echo "Nginx is required for the $1 role but is not installed."
        echo "Install nginx and re-run this script. For Debian/Ubuntu:"
        echo "  sudo apt-get update && sudo apt-get install nginx"
        exit 1
    fi
}

require_redis() {
    if ! command -v redis-cli >/dev/null 2>&1; then
        echo "Redis is required for the $1 role but is not installed."
        echo "Install redis-server and re-run this script. For Debian/Ubuntu:"
        echo "  sudo apt-get update && sudo apt-get install redis-server"
        exit 1
    fi
    if ! redis-cli ping >/dev/null 2>&1; then
        echo "Redis is required for the $1 role but does not appear to be running."
        echo "Start redis and re-run this script. For Debian/Ubuntu:"
        echo "  sudo systemctl start redis-server"
        exit 1
    fi
    cat > "$BASE_DIR/redis.env" <<'EOF'
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
EOF
}

check_nginx_and_redis() {
    local role="$1"
    local missing=()

    if ! ensure_nginx_in_path; then
        missing+=("nginx")
    fi
    if ! command -v redis-cli >/dev/null 2>&1; then
        missing+=("redis-server")
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        if [ ${#missing[@]} -eq 1 ]; then
            echo "${missing[0]} is required for the $role role but is not installed."
        else
            echo "${missing[*]} are required for the $role role but are not installed."
        fi
        echo "Install ${missing[*]} and re-run this script. For Debian/Ubuntu:"
        echo "  sudo apt-get update && sudo apt-get install ${missing[*]}"
        exit 1
    fi

    if ! redis-cli ping >/dev/null 2>&1; then
        echo "Redis is required for the $role role but does not appear to be running."
        echo "Start redis and re-run this script. For Debian/Ubuntu:"
        echo "  sudo systemctl start redis-server"
        exit 1
    fi
    cat > "$BASE_DIR/redis.env" <<'EOF'
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
EOF
}

ensure_i2c_packages() {
    if ! python3 -c 'import smbus' >/dev/null 2>&1 \
        && ! python3 -c 'import smbus2' >/dev/null 2>&1; then
        echo "smbus module not found. Installing i2c-tools and python3-smbus"
        sudo apt-get update
        sudo apt-get install -y i2c-tools python3-smbus
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --service)
            [ -z "$2" ] && usage
            SERVICE="$2"
            shift 2
            ;;
        --internal)
            NGINX_MODE="internal"
            shift
            ;;
        --public)
            NGINX_MODE="public"
            shift
            ;;
        --port)
            [ -z "$2" ] && usage
            PORT="$2"
            shift 2
            ;;
        --upgrade)
            UPGRADE=true
            shift
            ;;
        --auto-upgrade)
            AUTO_UPGRADE=true
            shift
            ;;
        --latest)
            LATEST=true
            shift
            ;;
        --stable)
            STABLE=true
            shift
            ;;
        --celery)
            ENABLE_CELERY=true
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
        --lcd-screen)
            ENABLE_LCD_SCREEN=true
            DISABLE_LCD_SCREEN=false
            shift
            ;;
        --no-lcd-screen)
            ENABLE_LCD_SCREEN=false
            DISABLE_LCD_SCREEN=true
            shift
            ;;
        --clean)
            CLEAN=true
            shift
            ;;
        --start)
            START_SERVICES=true
            shift
            ;;
        --satellite)
            require_nginx "satellite"
            AUTO_UPGRADE=true
            NGINX_MODE="internal"
            SERVICE="arthexis"
            LATEST=false
            ENABLE_CELERY=true
            NODE_ROLE="Satellite"
            REQUIRES_REDIS=true
            shift
            ;;
        --terminal)
            AUTO_UPGRADE=false
            NGINX_MODE="internal"
            SERVICE="arthexis"
            LATEST=true
            ENABLE_CELERY=true
            NODE_ROLE="Terminal"
            shift
            ;;
        --control)
            require_nginx "control"
            AUTO_UPGRADE=true
            NGINX_MODE="internal"
            SERVICE="arthexis"
            LATEST=true
            ENABLE_CELERY=true
            ENABLE_LCD_SCREEN=true
            DISABLE_LCD_SCREEN=false
            ENABLE_CONTROL=true
            NODE_ROLE="Control"
            REQUIRES_REDIS=true
            shift
            ;;
        --watchtower)
            require_nginx "watchtower"
            AUTO_UPGRADE=true
            NGINX_MODE="public"
            SERVICE="arthexis"
            ENABLE_CELERY=true
            LATEST=false
            NODE_ROLE="Watchtower"
            REQUIRES_REDIS=true
            shift
            ;;
        --constellation)
            echo "The Constellation role has been renamed to Watchtower." >&2
            echo "Use --watchtower for future invocations." >&2
            require_nginx "watchtower"
            AUTO_UPGRADE=true
            NGINX_MODE="public"
            SERVICE="arthexis"
            ENABLE_CELERY=true
            LATEST=false
            NODE_ROLE="Watchtower"
            REQUIRES_REDIS=true
            shift
            ;;
        *)
            usage
            ;;
    esac
done

if [ "$LATEST" = true ] && [ "$STABLE" = true ]; then
    echo "--stable cannot be used together with --latest." >&2
    exit 1
fi

if [ -z "$PORT" ]; then
    if [ "$NGINX_MODE" = "public" ]; then
        PORT=8000
    else
        PORT=8888
    fi
fi

DATASETTE_PORT=$((PORT + 1))

BASE_DIR="$SCRIPT_DIR"
cd "$BASE_DIR"
DB_FILE="$BASE_DIR/db.sqlite3"
if [ -f "$DB_FILE" ]; then
    if [ "$CLEAN" = true ]; then
        BACKUP_DIR="$BASE_DIR/backups"
        mkdir -p "$BACKUP_DIR"
        VERSION="unknown"
        [ -f "$BASE_DIR/VERSION" ] && VERSION="$(cat "$BASE_DIR/VERSION")"
        REVISION="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
        STAMP="$(date +%Y%m%d%H%M%S)"
        cp "$DB_FILE" "$BACKUP_DIR/db.sqlite3.${VERSION}.${REVISION}.${STAMP}.bak"
        rm "$DB_FILE"
    else
        echo "Database file $DB_FILE exists. Use --clean to remove it before installing." >&2
        exit 1
    fi
fi
LOCK_DIR="$BASE_DIR/locks"
mkdir -p "$LOCK_DIR"

if [ "$ENABLE_CONTROL" = true ]; then
    check_nginx_and_redis "$NODE_ROLE"
elif [ "$REQUIRES_REDIS" = true ]; then
    require_redis "$NODE_ROLE"
fi

if [ "$ENABLE_CELERY" = true ]; then
    touch "$LOCK_DIR/celery.lck"
else
    rm -f "$LOCK_DIR/celery.lck"
fi

LCD_LOCK="$LOCK_DIR/lcd_screen.lck"
if [ "$ENABLE_LCD_SCREEN" = true ]; then
    touch "$LCD_LOCK"
    ensure_i2c_packages
else
    rm -f "$LCD_LOCK"
fi

CONTROL_LOCK="$LOCK_DIR/control.lck"
if [ "$ENABLE_CONTROL" = true ]; then
    touch "$CONTROL_LOCK"
else
    rm -f "$CONTROL_LOCK"
fi

RFID_LOCK="$LOCK_DIR/rfid.lck"
if [ "$ENABLE_CONTROL" != true ]; then
    rm -f "$RFID_LOCK"
fi

DATASETTE_LOCK="$LOCK_DIR/datasette.lck"
if [ "$ENABLE_DATASETTE" = true ]; then
    touch "$DATASETTE_LOCK"
else
    rm -f "$DATASETTE_LOCK"
fi

# Create virtual environment if missing
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi

# Install nginx configuration and reload
echo "$NGINX_MODE" > "$LOCK_DIR/nginx_mode.lck"
echo "$NODE_ROLE" > "$LOCK_DIR/role.lck"
NGINX_CONF="/etc/nginx/conf.d/arthexis-${NGINX_MODE}.conf"

# Ensure nginx config directory exists
sudo mkdir -p /etc/nginx/conf.d

# Remove existing nginx configs for arthexis* (run in root shell to expand wildcard)
sudo sh -c 'rm -f /etc/nginx/conf.d/arthexis-*.conf'

FALLBACK_SRC_DIR="$BASE_DIR/config/data/nginx/maintenance"
FALLBACK_DEST_DIR="/usr/share/arthexis-fallback"
if [ -d "$FALLBACK_SRC_DIR" ]; then
    sudo mkdir -p "$FALLBACK_DEST_DIR"
    sudo cp -r "$FALLBACK_SRC_DIR"/. "$FALLBACK_DEST_DIR"/
fi

if [ "$NGINX_MODE" = "public" ]; then
    sudo tee "$NGINX_CONF" > /dev/null <<'NGINXCONF'
# Redirect all HTTP traffic to HTTPS
server {
    listen 80;
    server_name arthexis.com *.arthexis.com;
    return 301 https://$host$request_uri;
}

# HTTPS Server
server {
    listen 443 ssl;
    server_name arthexis.com *.arthexis.com;

    error_page 500 502 503 504 /maintenance/index.html;

    location = /maintenance/index.html {
        root /usr/share/arthexis-fallback;
        add_header Cache-Control "no-store";
    }

    location /maintenance/ {
        alias /usr/share/arthexis-fallback/;
        add_header Cache-Control "no-store";
    }

    ssl_certificate /etc/letsencrypt/live/arthexis.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/arthexis.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # Default proxy to web app
    location / {
        proxy_pass http://127.0.0.1:PORT_PLACEHOLDER;
        proxy_intercept_errors on;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Model Context Protocol server (SSE)
    location = /mcp {
        return 301 /mcp/;
    }

    location /mcp/ {
        proxy_pass http://127.0.0.1:MCP_PORT_PLACEHOLDER/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Cache-Control "no-cache";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600;
    }
    #DATASETTE_START
    location /data/ {
        auth_request /datasette-auth/;
        error_page 401 =302 /login/?next=$request_uri;
        proxy_pass http://127.0.0.1:DATA_PORT_PLACEHOLDER/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    #DATASETTE_END
}
NGINXCONF
else
    sudo tee "$NGINX_CONF" > /dev/null <<'NGINXCONF'
server {
    listen 8000;
    listen 8080;
    server_name _;

    error_page 500 502 503 504 /maintenance/index.html;

    location = /maintenance/index.html {
        root /usr/share/arthexis-fallback;
        add_header Cache-Control "no-store";
    }

    location /maintenance/ {
        alias /usr/share/arthexis-fallback/;
        add_header Cache-Control "no-store";
    }

    location / {
        proxy_pass http://127.0.0.1:PORT_PLACEHOLDER;
        proxy_intercept_errors on;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Model Context Protocol server (SSE)
    location = /mcp {
        return 301 /mcp/;
    }

    location /mcp/ {
        proxy_pass http://127.0.0.1:MCP_PORT_PLACEHOLDER/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Cache-Control "no-cache";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600;
    }
    #DATASETTE_START
    location /data/ {
        auth_request /datasette-auth/;
        error_page 401 =302 /login/?next=$request_uri;
        proxy_pass http://127.0.0.1:DATA_PORT_PLACEHOLDER/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    #DATASETTE_END
}
NGINXCONF
fi

sudo sed -i "s/PORT_PLACEHOLDER/$PORT/" "$NGINX_CONF"
MCP_PROXY_PORT="${MCP_SIGIL_PORT:-8800}"
if [[ "$MCP_PROXY_PORT" =~ ^MCP_[0-9]+$ ]]; then
    MCP_PROXY_PORT="${MCP_PROXY_PORT#MCP_}"
fi
if [[ ! "$MCP_PROXY_PORT" =~ ^[0-9]+$ ]]; then
    echo "Invalid MCP_SIGIL_PORT value: '$MCP_PROXY_PORT'. Expected a numeric port."
    exit 1
fi
sudo sed -i "s/MCP_PORT_PLACEHOLDER/$MCP_PROXY_PORT/" "$NGINX_CONF"
if [ "$ENABLE_DATASETTE" = true ]; then
    sudo sed -i "s/DATA_PORT_PLACEHOLDER/$DATASETTE_PORT/" "$NGINX_CONF"
    sudo sed -i '/#DATASETTE_START/d;/#DATASETTE_END/d' "$NGINX_CONF"
else
    sudo sed -i '/#DATASETTE_START/,/#DATASETTE_END/d' "$NGINX_CONF"
fi

if arthexis_can_manage_nginx; then
    arthexis_refresh_nginx_maintenance "$SCRIPT_DIR" "$NGINX_CONF"
fi

if arthexis_ensure_nginx_in_path && command -v nginx >/dev/null 2>&1; then
    sudo nginx -t
    sudo systemctl reload nginx || echo "Warning: nginx reload failed"
else
    echo "nginx not installed; skipping nginx test and reload"
fi

source .venv/bin/activate
pip install --upgrade pip

REQ_FILE="requirements.txt"
MD5_FILE="requirements.md5"
NEW_HASH=$(md5sum "$REQ_FILE" | awk '{print $1}')
STORED_HASH=""
[ -f "$MD5_FILE" ] && STORED_HASH=$(cat "$MD5_FILE")
if [ "$NEW_HASH" != "$STORED_HASH" ]; then
    if [ -f "$PIP_INSTALL_HELPER" ] && command -v python >/dev/null 2>&1; then
        python "$PIP_INSTALL_HELPER" -r "$REQ_FILE"
    else
        pip install -r "$REQ_FILE"
    fi
    echo "$NEW_HASH" > "$MD5_FILE"
else
    echo "Requirements unchanged. Skipping installation."
fi

if [ "$ENABLE_DATASETTE" = true ]; then
    if [ -f "$PIP_INSTALL_HELPER" ] && command -v python >/dev/null 2>&1; then
        python "$PIP_INSTALL_HELPER" datasette
    else
        pip install datasette
    fi
fi

if [ "$ENABLE_CONTROL" = true ]; then
    echo "Checking for RFID scanner hardware..."
    if python -m ocpp.rfid.detect; then
        touch "$RFID_LOCK"
        echo "Enabled node feature 'rfid-scanner' based on detected hardware."
    else
        rm -f "$RFID_LOCK"
        echo "Skipped enabling 'rfid-scanner'; hardware not detected during install."
    fi
fi

python manage.py migrate --noinput

# Load personal user data fixtures if present
if ls data/*.json >/dev/null 2>&1; then
    python manage.py loaddata data/*.json
fi

# Refresh environment data and register this node
if [ "$LATEST" = true ]; then
    ./env-refresh.sh --latest
else
    ./env-refresh.sh
fi

deactivate


# If a service name was provided, install a systemd unit and persist its name
if [ -n "$SERVICE" ]; then
    SERVICE_FILE="/etc/systemd/system/${SERVICE}.service"
    echo "$SERVICE" > "$LOCK_DIR/service.lck"
    EXEC_CMD="$BASE_DIR/.venv/bin/python manage.py runserver 0.0.0.0:$PORT"
    sudo bash -c "cat > '$SERVICE_FILE'" <<SERVICEEOF
[Unit]
Description=Arthexis Constellation Django service
After=network.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
EnvironmentFile=-$BASE_DIR/redis.env
ExecStart=$EXEC_CMD
Restart=always
User=$(id -un)

[Install]
WantedBy=multi-user.target
SERVICEEOF
    if [ "$ENABLE_CELERY" = true ]; then
        CELERY_SERVICE="celery-$SERVICE"
        CELERY_SERVICE_FILE="/etc/systemd/system/${CELERY_SERVICE}.service"
        sudo bash -c "cat > '$CELERY_SERVICE_FILE'" <<CELERYSERVICEEOF
[Unit]
Description=Celery Worker for $SERVICE
After=network.target redis.service

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
EnvironmentFile=-$BASE_DIR/redis.env
ExecStart=$BASE_DIR/.venv/bin/celery -A config worker -l info
Restart=always
User=$(id -un)

[Install]
WantedBy=multi-user.target
CELERYSERVICEEOF
        CELERY_BEAT_SERVICE="celery-beat-$SERVICE"
        CELERY_BEAT_SERVICE_FILE="/etc/systemd/system/${CELERY_BEAT_SERVICE}.service"
        sudo bash -c "cat > '$CELERY_BEAT_SERVICE_FILE'" <<BEATSERVICEEOF
[Unit]
Description=Celery Beat for $SERVICE
After=network.target redis.service

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
EnvironmentFile=-$BASE_DIR/redis.env
ExecStart=$BASE_DIR/.venv/bin/celery -A config beat -l info
Restart=always
User=$(id -un)

[Install]
WantedBy=multi-user.target
BEATSERVICEEOF
    fi
    if [ "$ENABLE_DATASETTE" = true ]; then
        DATASETTE_SERVICE="datasette-$SERVICE"
        DATASETTE_SERVICE_FILE="/etc/systemd/system/${DATASETTE_SERVICE}.service"
        sudo bash -c "cat > '$DATASETTE_SERVICE_FILE'" <<DATASETTESERVICEEOF
[Unit]
Description=Datasette for $SERVICE
After=network.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
ExecStart=$BASE_DIR/.venv/bin/datasette serve $DB_FILE --host 127.0.0.1 --port $DATASETTE_PORT --setting base_url /data/
Restart=always
User=$(id -un)

[Install]
WantedBy=multi-user.target
DATASETTESERVICEEOF
    fi
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE"
    if [ "$ENABLE_CELERY" = true ]; then
        sudo systemctl enable "$CELERY_SERVICE" "$CELERY_BEAT_SERVICE"
    fi
    if [ "$ENABLE_DATASETTE" = true ]; then
        sudo systemctl enable "$DATASETTE_SERVICE"
        sudo systemctl restart "$DATASETTE_SERVICE"
    fi
fi

if [ "$ENABLE_LCD_SCREEN" = true ] && [ -n "$SERVICE" ]; then
    LCD_SERVICE="lcd-$SERVICE"
    LCD_SERVICE_FILE="/etc/systemd/system/${LCD_SERVICE}.service"
    sudo bash -c "cat > '$LCD_SERVICE_FILE'" <<SERVICEEOF
[Unit]
Description=LCD screen updater service for Arthexis
After=network.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
ExecStart=$BASE_DIR/.venv/bin/python -m core.lcd_screen
Restart=always
User=$(id -un)

[Install]
WantedBy=multi-user.target
SERVICEEOF
    sudo systemctl daemon-reload
    sudo systemctl enable "$LCD_SERVICE"
elif [ "$DISABLE_LCD_SCREEN" = true ]; then
    if [ -z "$SERVICE" ] && [ -f "$LOCK_DIR/service.lck" ]; then
        SERVICE="$(cat "$LOCK_DIR/service.lck")"
    fi
    if [ -n "$SERVICE" ]; then
        LCD_SERVICE="lcd-$SERVICE"
        if systemctl list-unit-files | grep -Fq "${LCD_SERVICE}.service"; then
            sudo systemctl stop "$LCD_SERVICE" || true
            sudo systemctl disable "$LCD_SERVICE" || true
            LCD_SERVICE_FILE="/etc/systemd/system/${LCD_SERVICE}.service"
            if [ -f "$LCD_SERVICE_FILE" ]; then
                sudo rm "$LCD_SERVICE_FILE"
            fi
            sudo systemctl daemon-reload
        fi
    fi
fi

if [ "$ENABLE_DATASETTE" != true ]; then
    if [ -z "$SERVICE" ] && [ -f "$LOCK_DIR/service.lck" ]; then
        SERVICE="$(cat "$LOCK_DIR/service.lck")"
    fi
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

if [ "$AUTO_UPGRADE" = true ]; then
    rm -f AUTO_UPGRADE
    AUTO_UPGRADE_MODE="version"
    if [ "$LATEST" = true ]; then
        AUTO_UPGRADE_MODE="latest"
    elif [ "$STABLE" = true ]; then
        AUTO_UPGRADE_MODE="stable"
    fi
    echo "$AUTO_UPGRADE_MODE" > "$LOCK_DIR/auto_upgrade.lck"
    if [ "$UPGRADE" = true ]; then
        if [ "$LATEST" = true ]; then
            ./upgrade.sh --latest
        elif [ "$STABLE" = true ]; then
            ./upgrade.sh --stable
        else
            ./upgrade.sh
        fi
    fi
    source .venv/bin/activate
    python manage.py shell <<'PYCODE'
from core.auto_upgrade import ensure_auto_upgrade_periodic_task

ensure_auto_upgrade_periodic_task()
PYCODE
    deactivate
elif [ "$UPGRADE" = true ]; then
    if [ "$LATEST" = true ]; then
        ./upgrade.sh --latest
    elif [ "$STABLE" = true ]; then
        ./upgrade.sh --stable
    else
        ./upgrade.sh
    fi
elif [ -n "$SERVICE" ]; then
    sudo systemctl restart "$SERVICE"
    if [ "$ENABLE_CELERY" = true ]; then
        sudo systemctl restart "celery-$SERVICE"
        sudo systemctl restart "celery-beat-$SERVICE"
    fi
    if [ "$ENABLE_DATASETTE" = true ]; then
        sudo systemctl restart "datasette-$SERVICE"
    fi
fi

if [ "$START_SERVICES" = true ]; then
    "$BASE_DIR/start.sh"
fi

