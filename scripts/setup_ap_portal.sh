#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORTAL_PORT="${PORTAL_PORT:-9080}"
CURRENT_AP_NAME="${CURRENT_AP_NAME:-arthexis-ap}"
TARGET_AP_NAME="${TARGET_AP_NAME:-arthexis-1}"
STATE_DIR="${STATE_DIR:-$BASE_DIR/.state/ap_portal}"
SERVICE_FILE="/etc/systemd/system/arthexis-ap-portal.service"
NGINX_SITE="/etc/nginx/sites-enabled/arthexis.conf"
NGINX_BACKUP_DIR="/etc/nginx/sites-available"
DEFAULT_CERT_PATH="/etc/letsencrypt/live/arthexis.com/fullchain.pem"
DEFAULT_KEY_PATH="/etc/letsencrypt/live/arthexis.com/privkey.pem"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

clear_wifi_secrets() {
    local conn_name="$1"

    nmcli connection modify "$conn_name" remove 802-11-wireless-security >/dev/null 2>&1 || true
    nmcli connection modify "$conn_name" remove 802-1x >/dev/null 2>&1 || true
}

require_binary() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

require_binary nmcli
require_binary nft
require_binary nginx
require_binary systemctl

if [[ ! -x "$BASE_DIR/.venv/bin/python" ]]; then
    echo "Missing virtualenv Python at $BASE_DIR/.venv/bin/python" >&2
    exit 1
fi

mkdir -p "$STATE_DIR"

ap_connection="$TARGET_AP_NAME"
if ! nmcli -t -f NAME connection show | grep -Fxq "$ap_connection"; then
    if nmcli -t -f NAME connection show | grep -Fxq "$CURRENT_AP_NAME"; then
        ap_connection="$CURRENT_AP_NAME"
    else
        echo "Could not find AP connection '$CURRENT_AP_NAME' or '$TARGET_AP_NAME'." >&2
        exit 1
    fi
fi

ap_uuid="$(nmcli -g connection.uuid connection show "$ap_connection" | head -n1 | tr -d '\r')"
if [[ -z "$ap_uuid" ]]; then
    echo "Unable to resolve AP connection UUID for '$ap_connection'." >&2
    exit 1
fi

timestamp="$(date +%Y%m%d%H%M%S)"
rm -f /etc/nginx/sites-enabled/arthexis.conf.pre-ap-portal-*
if [[ -f "$NGINX_SITE" ]]; then
    cp "$NGINX_SITE" "${NGINX_BACKUP_DIR}/arthexis.conf.pre-ap-portal-${timestamp}"
fi

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Arthexis AP consent portal
After=network-online.target NetworkManager.service nginx.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
ExecStart=$BASE_DIR/.venv/bin/python $BASE_DIR/scripts/ap_portal_server.py --bind 127.0.0.1 --port $PORTAL_PORT --state-dir $STATE_DIR
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

HTTPS_BLOCK=""
if [[ -f "$DEFAULT_CERT_PATH" && -f "$DEFAULT_KEY_PATH" ]]; then
    HTTPS_BLOCK="$(cat <<EOF
server {
    listen 443 ssl;
    server_name _;
    ssl_certificate $DEFAULT_CERT_PATH;
    ssl_certificate_key $DEFAULT_KEY_PATH;
    include $BASE_DIR/apps/nginx/options-ssl-nginx.conf;
    add_header Content-Security-Policy "upgrade-insecure-requests; block-all-mixed-content" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;

    location / {
        proxy_pass http://127.0.0.1:8888;
        proxy_intercept_errors on;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_read_timeout 1d;
        proxy_send_timeout 1d;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
)"
fi

cat > "$NGINX_SITE" <<EOF
map \$http_upgrade \$connection_upgrade {
    default upgrade;
    '' close;
}

server {
    listen 0.0.0.0:80 default_server;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:$PORTAL_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}

server {
    listen 0.0.0.0:8000;
    listen 0.0.0.0:8080;
    listen 0.0.0.0:8900;
    server_name _;

    location / {
        set \$simulator_redirect "";
        if (\$server_port = 8900) {
            set \$simulator_redirect \$uri;
        }
        if (\$simulator_redirect = "/") {
            return 302 /ocpp/evcs/simulator/;
        }
        proxy_pass http://127.0.0.1:8888;
        proxy_intercept_errors on;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_read_timeout 1d;
        proxy_send_timeout 1d;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
$HTTPS_BLOCK
EOF

systemctl daemon-reload

nginx -t
systemctl enable --now nginx
systemctl restart nginx
systemctl enable --now arthexis-ap-portal.service

nmcli connection modify "$ap_uuid" \
    connection.id "$TARGET_AP_NAME" \
    connection.interface-name wlan0 \
    802-11-wireless.ssid "$TARGET_AP_NAME" \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    ipv4.method shared \
    ipv4.addresses 10.42.0.1/16 \
    ipv4.never-default yes \
    ipv6.method shared \
    ipv6.addresses fd42:0:0:42::1/64 \
    ipv6.never-default yes \
    connection.autoconnect yes

clear_wifi_secrets "$TARGET_AP_NAME"
nmcli connection up "$TARGET_AP_NAME" ifname wlan0
echo "Configured AP '$TARGET_AP_NAME' as an open captive portal."
echo "Consent records will be stored at: $STATE_DIR/consents.jsonl"
