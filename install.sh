#!/usr/bin/env bash
set -e

SERVICE=""
SETUP_NGINX=false
NGINX_MODE=""
PORT=""
AUTO_UPGRADE=false
LATEST=false

usage() {
    echo "Usage: $0 [--service NAME] [--nginx] [--public|--internal] [--port PORT] [--auto-upgrade] [--latest] [--satellite]" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --service)
            [ -z "$2" ] && usage
            SERVICE="$2"
            shift 2
            ;;
        --nginx)
            SETUP_NGINX=true
            shift
            ;;
        --internal)
            SETUP_NGINX=true
            NGINX_MODE="internal"
            shift
            ;;
        --public)
            SETUP_NGINX=true
            NGINX_MODE="public"
            shift
            ;;
        --port)
            [ -z "$2" ] && usage
            PORT="$2"
            shift 2
            ;;
        --auto-upgrade)
            AUTO_UPGRADE=true
            shift
            ;;
        --latest)
            LATEST=true
            shift
            ;;
        --satellite)
            AUTO_UPGRADE=true
            SETUP_NGINX=true
            NGINX_MODE="internal"
            SERVICE="arthexis"
            LATEST=true
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

if [ "$SETUP_NGINX" = true ] && [ -z "$NGINX_MODE" ]; then
    NGINX_MODE="internal"
fi

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

# Create virtual environment if missing
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi

# If requested, install nginx configuration and reload
if [ "$SETUP_NGINX" = true ]; then
    echo "$NGINX_MODE" > NGINX_MODE
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

deactivate


# If a service name was provided, install a systemd unit and persist its name
if [ -n "$SERVICE" ]; then
    SERVICE_FILE="/etc/systemd/system/${SERVICE}.service"
    echo "$SERVICE" > SERVICE
    sudo bash -c "cat > '$SERVICE_FILE'" <<SERVICEEOF
[Unit]
Description=Arthexis Constellation Django service
After=network.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
ExecStart=$BASE_DIR/.venv/bin/python manage.py runserver 0.0.0.0:$PORT
Restart=always
User=$(id -un)

[Install]
WantedBy=multi-user.target
SERVICEEOF
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE"
fi

if [ "$AUTO_UPGRADE" = true ]; then
    if [ "$LATEST" = true ]; then
        echo "latest" > AUTO_UPGRADE
        ./upgrade.sh --latest
    else
        echo "version" > AUTO_UPGRADE
        ./upgrade.sh
    fi
    source .venv/bin/activate
    python manage.py shell <<'PYCODE'
from django_celery_beat.models import IntervalSchedule, PeriodicTask

schedule, _ = IntervalSchedule.objects.get_or_create(
    every=10, period=IntervalSchedule.MINUTES
)
PeriodicTask.objects.update_or_create(
    name="auto_upgrade_check",
    defaults={"interval": schedule, "task": "release.tasks.check_github_updates"},
)
PYCODE
    deactivate
else
    if [ -n "$SERVICE" ]; then
        sudo systemctl restart "$SERVICE"
    fi
fi

