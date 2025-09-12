#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

SERVICE=""
NGINX_MODE="internal"
PORT=""
AUTO_UPGRADE=false
LATEST=false
UPGRADE=false
ENABLE_CELERY=false
ENABLE_LCD_SCREEN=false
DISABLE_LCD_SCREEN=false
CLEAN=false
ENABLE_CONTROL=false
NODE_ROLE="Terminal"
REQUIRES_REDIS=false
ENABLE_DATASETTE=false

usage() {
    echo "Usage: $0 [--service NAME] [--public|--internal] [--port PORT] [--upgrade] [--auto-upgrade] [--latest] [--satellite] [--terminal] [--control] [--constellation] [--celery] [--lcd-screen|--no-lcd-screen] [--datasette] [--clean]" >&2
    exit 1
}

require_nginx() {
    if ! command -v nginx >/dev/null 2>&1; then
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

    if ! command -v nginx >/dev/null 2>&1; then
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
        --celery)
            ENABLE_CELERY=true
            shift
            ;;
        --datasette)
            ENABLE_DATASETTE=true
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
        --constellation)
            require_nginx "constellation"
            AUTO_UPGRADE=true
            NGINX_MODE="public"
            SERVICE="arthexis"
            ENABLE_CELERY=true
            LATEST=false
            NODE_ROLE="Constellation"
            REQUIRES_REDIS=true
            shift
            ;;
        *)
            usage
            ;;
    esac
done

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
else
    rm -f "$LCD_LOCK"
fi

CONTROL_LOCK="$LOCK_DIR/control.lck"
if [ "$ENABLE_CONTROL" = true ]; then
    touch "$CONTROL_LOCK"
else
    rm -f "$CONTROL_LOCK"
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

    ssl_certificate /etc/letsencrypt/live/arthexis.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/arthexis.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # Default proxy to web app
    location / {
        proxy_pass http://127.0.0.1:PORT_PLACEHOLDER;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
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
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:PORT_PLACEHOLDER;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
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
if [ "$ENABLE_DATASETTE" = true ]; then
    sudo sed -i "s/DATA_PORT_PLACEHOLDER/$DATASETTE_PORT/" "$NGINX_CONF"
    sudo sed -i '/#DATASETTE_START/d;/#DATASETTE_END/d' "$NGINX_CONF"
else
    sudo sed -i '/#DATASETTE_START/,/#DATASETTE_END/d' "$NGINX_CONF"
fi

if command -v nginx >/dev/null 2>&1; then
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
    pip install -r "$REQ_FILE"
    echo "$NEW_HASH" > "$MD5_FILE"
else
    echo "Requirements unchanged. Skipping installation."
fi

if [ "$ENABLE_DATASETTE" = true ]; then
    pip install datasette
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
    if [ "$LATEST" = true ]; then
        echo "latest" > AUTO_UPGRADE
    else
        echo "version" > AUTO_UPGRADE
    fi
    if [ "$UPGRADE" = true ]; then
        if [ "$LATEST" = true ]; then
            ./upgrade.sh --latest
        else
            ./upgrade.sh
        fi
    fi
    source .venv/bin/activate
    python manage.py shell <<'PYCODE'
from django_celery_beat.models import IntervalSchedule, PeriodicTask
from django.utils.text import slugify

schedule, _ = IntervalSchedule.objects.get_or_create(
    every=10, period=IntervalSchedule.MINUTES
)
PeriodicTask.objects.update_or_create(
    name=slugify("auto upgrade check"),
    defaults={"interval": schedule, "task": "core.tasks.check_github_updates"},
)
PYCODE
    deactivate
elif [ "$UPGRADE" = true ]; then
    if [ "$LATEST" = true ]; then
        ./upgrade.sh --latest
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

