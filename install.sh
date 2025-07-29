#!/usr/bin/env bash
set -e

SERVICE=""

usage() {
    echo "Usage: $0 [--service NAME]" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --service)
            [ -z "$2" ] && usage
            SERVICE="$2"
            shift 2
            ;;
        *)
            usage
            ;;
    esac
done

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

# Create virtual environment if missing
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

deactivate

if [ -n "$SERVICE" ]; then
    SERVICE_FILE="/etc/systemd/system/${SERVICE}.service"
    sudo bash -c "cat > '$SERVICE_FILE'" <<SERVICEEOF
[Unit]
Description=Arthexis Django service
After=network.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
ExecStart=$BASE_DIR/.venv/bin/python manage.py runserver 0.0.0.0:8000
Restart=always
User=$(id -un)

[Install]
WantedBy=multi-user.target
SERVICEEOF
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE"
    sudo systemctl restart "$SERVICE"
fi
