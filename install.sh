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

# Ensure envs directory exists for environment files
mkdir -p envs

# Create virtual environment if missing
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip

REQ_FILE="requirements.txt"
pip install -r "$REQ_FILE"

deactivate

# If a service name was provided, install a systemd unit and persist its name
if [ -n "$SERVICE" ]; then
    SERVICE_FILE="/etc/systemd/system/${SERVICE}.service"
    echo "$SERVICE" > envs/service
    sudo bash -c "cat > '$SERVICE_FILE'" <<SERVICEEOF
[Unit]
Description=Arthexis Constellation Django service
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

