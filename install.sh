#!/usr/bin/env bash
set -e

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

usage() {
    echo "Usage: $0 [--service NAME] [--public|--internal] [--port PORT] [--upgrade] [--auto-upgrade] [--latest] [--satellite] [--terminal] [--control] [--constellation] [--celery] [--lcd-screen|--no-lcd-screen] [--clean]" >&2
    exit 1
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
            AUTO_UPGRADE=true
            NGINX_MODE="internal"
            SERVICE="arthexis"
            LATEST=true
            ENABLE_CELERY=true
            NODE_ROLE="Gateway"
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
            shift
            ;;
        --constellation)
            AUTO_UPGRADE=true
            NGINX_MODE="public"
            SERVICE="arthexis"
            ENABLE_CELERY=true
            LATEST=false
            NODE_ROLE="Constellation"
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

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"
DB_FILE="$BASE_DIR/db.sqlite3"
if [ -f "$DB_FILE" ]; then
    if [ "$CLEAN" = true ]; then
        rm "$DB_FILE"
    else
        echo "Database file $DB_FILE exists. Use --clean to remove it before installing." >&2
        exit 1
    fi
fi
LOCK_DIR="$BASE_DIR/locks"
mkdir -p "$LOCK_DIR"

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
}
NGINXCONF
fi

sudo sed -i "s/PORT_PLACEHOLDER/$PORT/" "$NGINX_CONF"

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

python manage.py migrate --noinput

deactivate


# If a service name was provided, install a systemd unit and persist its name
if [ -n "$SERVICE" ]; then
    SERVICE_FILE="/etc/systemd/system/${SERVICE}.service"
    echo "$SERVICE" > "$LOCK_DIR/service.lck"
    if [ "$ENABLE_CELERY" = true ]; then
        EXEC_CMD="/bin/sh -c 'cd $BASE_DIR && .venv/bin/celery -A config worker -l info & .venv/bin/celery -A config beat -l info & exec .venv/bin/python manage.py runserver 0.0.0.0:$PORT'"
    else
        EXEC_CMD="$BASE_DIR/.venv/bin/python manage.py runserver 0.0.0.0:$PORT"
    fi
    sudo bash -c "cat > '$SERVICE_FILE'" <<SERVICEEOF
[Unit]
Description=Arthexis Constellation Django service
After=network.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
ExecStart=$EXEC_CMD
Restart=always
User=$(id -un)

[Install]
WantedBy=multi-user.target
SERVICEEOF
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE"
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
    defaults={"interval": schedule, "task": "release.tasks.check_github_updates"},
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
fi

