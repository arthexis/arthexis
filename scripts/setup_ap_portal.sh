#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORTAL_PORT="${PORTAL_PORT:-9080}"
CURRENT_AP_NAME="${CURRENT_AP_NAME:-arthexis-ap}"
TARGET_AP_NAME="${TARGET_AP_NAME:-arthexis-1}"
STATE_DIR="${STATE_DIR:-$BASE_DIR/.state/ap_portal}"
SOURCE_URL="${SOURCE_URL:-https://github.com/arthexis/arthexis/blob/main/scripts/ap_portal_server.py}"
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

ensure_ap_profile() {
    if nmcli -t -f NAME connection show | grep -qx "$TARGET_AP_NAME"; then
        :
    elif nmcli -t -f NAME connection show | grep -qx "$CURRENT_AP_NAME"; then
        nmcli connection modify "$CURRENT_AP_NAME" connection.id "$TARGET_AP_NAME"
    else
        nmcli connection add type wifi ifname wlan0 con-name "$TARGET_AP_NAME" ssid "$TARGET_AP_NAME" \
            802-11-wireless.mode ap ipv4.method shared ipv4.addresses 10.42.0.1/16
    fi

    clear_wifi_secrets "$TARGET_AP_NAME"
    nmcli connection modify "$TARGET_AP_NAME" \
        802-11-wireless.ssid "$TARGET_AP_NAME" \
        802-11-wireless.mode ap \
        ipv4.method shared \
        ipv4.addresses 10.42.0.1/16 \
        ipv6.method shared \
        ipv6.addresses fd42:0:0:42::1/64 \
        connection.autoconnect yes
}

install_service() {
    mkdir -p "$STATE_DIR"
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Arthexis AP consent and activity monitoring portal
After=network-online.target NetworkManager.service nginx.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
ExecStart=$BASE_DIR/.venv/bin/python $BASE_DIR/scripts/ap_portal_server.py --bind 127.0.0.1 --port $PORTAL_PORT --state-dir $STATE_DIR --source-url $SOURCE_URL
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
}

install_nginx_site() {
    local timestamp
    timestamp="$(date +%Y%m%d%H%M%S)"
    rm -f /etc/nginx/sites-enabled/arthexis.conf.pre-ap-portal-*
    if [[ -f "$NGINX_SITE" ]]; then
        mkdir -p "$NGINX_BACKUP_DIR"
        cp "$NGINX_SITE" "${NGINX_BACKUP_DIR}/arthexis.conf.pre-ap-portal-${timestamp}"
    fi

    local https_block=""
    if [[ -f "$DEFAULT_CERT_PATH" && -f "$DEFAULT_KEY_PATH" ]]; then
        https_block="$(cat <<EOF
server {
    listen 443 ssl;
    server_name _;
    ssl_certificate $DEFAULT_CERT_PATH;
    ssl_certificate_key $DEFAULT_KEY_PATH;
    include $BASE_DIR/apps/nginx/options-ssl-nginx.conf;
    add_header Content-Security-Policy "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'" always;
    location / {
        proxy_pass http://127.0.0.1:$PORTAL_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
)"
    fi

    cat > "$NGINX_SITE" <<EOF
server {
    listen 80 default_server;
    server_name _;
    add_header Content-Security-Policy "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'" always;
    location / {
        proxy_pass http://127.0.0.1:$PORTAL_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}

$https_block
EOF
}

ensure_ap_profile
install_service
install_nginx_site

nginx -t
systemctl daemon-reload
systemctl enable nginx arthexis-ap-portal.service
systemctl restart nginx arthexis-ap-portal.service
nmcli connection up "$TARGET_AP_NAME" ifname wlan0

echo "Arthexis AP portal is installed for SSID $TARGET_AP_NAME."
