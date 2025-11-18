#!/usr/bin/env bash
set -e

# Bootstrap logging and helper utilities used throughout the installation.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIP_INSTALL_HELPER="$SCRIPT_DIR/scripts/helpers/pip_install.py"
# shellcheck source=scripts/helpers/logging.sh
. "$SCRIPT_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/desktop_shortcuts.sh
. "$SCRIPT_DIR/scripts/helpers/desktop_shortcuts.sh"
# shellcheck source=scripts/helpers/version_marker.sh
. "$SCRIPT_DIR/scripts/helpers/version_marker.sh"
# shellcheck source=scripts/helpers/ports.sh
. "$SCRIPT_DIR/scripts/helpers/ports.sh"
# shellcheck source=scripts/helpers/systemd_locks.sh
. "$SCRIPT_DIR/scripts/helpers/systemd_locks.sh"
arthexis_resolve_log_dir "$SCRIPT_DIR" LOG_DIR || exit 1
# Write a copy of stdout/stderr to a dedicated log file for troubleshooting.
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

# Default configuration flags populated by CLI parsing below.
SERVICE=""
NGINX_MODE="internal"
PORT=""
AUTO_UPGRADE=true
CHANNEL="stable"
UPGRADE=false
ENABLE_CELERY=false
ENABLE_LCD_SCREEN=false
DISABLE_LCD_SCREEN=false
CLEAN=false
ENABLE_CONTROL=false
NODE_ROLE="Terminal"
REQUIRES_REDIS=false
START_SERVICES=false
REPAIR=false

usage() {
    echo "Usage: $0 [--service NAME] [--public|--internal] [--port PORT] [--upgrade] [--fixed] [--stable|--regular|--normal|--unstable|--latest] [--satellite] [--terminal] [--control] [--watchtower] [--celery] [--lcd-screen|--no-lcd-screen] [--clean] [--start] [--repair]" >&2
    exit 1
}

# Service management helpers to avoid lock conflicts during repair operations.
stop_systemd_unit_if_present() {
    local unit_name="$1"

    if ! command -v systemctl >/dev/null 2>&1; then
        return 0
    fi

    if systemctl list-unit-files | awk '{print $1}' | grep -Fxq "$unit_name"; then
        echo "Stopping ${unit_name} before repair to avoid database locks."
        sudo systemctl stop "$unit_name" || true
    fi
}

stop_existing_units_for_repair() {
    local service_name="$1"

    stop_systemd_unit_if_present "${service_name}.service"

    if [ "$ENABLE_CELERY" = true ]; then
        stop_systemd_unit_if_present "celery-${service_name}.service"
        stop_systemd_unit_if_present "celery-beat-${service_name}.service"
    fi
}

# Dependency checks for nginx and redis, populating redis.env when appropriate.
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

# Hardware support utilities.
ensure_i2c_packages() {
    if ! python3 -c 'import smbus' >/dev/null 2>&1 \
        && ! python3 -c 'import smbus2' >/dev/null 2>&1; then
        echo "smbus module not found. Installing i2c-tools and python3-smbus"
        sudo apt-get update
        sudo apt-get install -y i2c-tools python3-smbus
    fi
}

# Parse CLI arguments to configure the installation behavior.
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
        --fixed)
            AUTO_UPGRADE=false
            shift
            ;;
        --latest|--unstable)
            CHANNEL="unstable"
            shift
            ;;
        --stable|--regular|--normal)
            CHANNEL="stable"
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
        --start)
            START_SERVICES=true
            shift
            ;;
        --repair)
            REPAIR=true
            shift
            ;;
        --satellite)
            require_nginx "satellite"
            AUTO_UPGRADE=true
            NGINX_MODE="internal"
            SERVICE="arthexis"
            CHANNEL="stable"
            ENABLE_CELERY=true
            NODE_ROLE="Satellite"
            REQUIRES_REDIS=true
            shift
            ;;
        --terminal)
            AUTO_UPGRADE=true
            NGINX_MODE="internal"
            SERVICE="arthexis"
            CHANNEL="unstable"
            ENABLE_CELERY=true
            NODE_ROLE="Terminal"
            shift
            ;;
        --control)
            require_nginx "control"
            AUTO_UPGRADE=true
            NGINX_MODE="internal"
            SERVICE="arthexis"
            CHANNEL="unstable"
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
            CHANNEL="stable"
            NODE_ROLE="Watchtower"
            REQUIRES_REDIS=true
            shift
            ;;
        *)
            usage
            ;;
    esac
done

if [ "$REPAIR" = true ]; then
    LOCK_DIR_PATH="$SCRIPT_DIR/locks"
    if [ -z "$SERVICE" ] && [ -f "$LOCK_DIR_PATH/service.lck" ]; then
        SERVICE="$(cat "$LOCK_DIR_PATH/service.lck")"
        echo "Repair mode: reusing existing service '$SERVICE'."
    fi
    if [ -f "$LOCK_DIR_PATH/nginx_mode.lck" ]; then
        NGINX_MODE="$(cat "$LOCK_DIR_PATH/nginx_mode.lck")"
    fi
    if [ "$ENABLE_CELERY" = false ] && [ -f "$LOCK_DIR_PATH/celery.lck" ]; then
        ENABLE_CELERY=true
    fi
    if [ "$ENABLE_LCD_SCREEN" = false ] && [ -f "$LOCK_DIR_PATH/lcd_screen.lck" ]; then
        ENABLE_LCD_SCREEN=true
        DISABLE_LCD_SCREEN=false
    fi
    if [ "$ENABLE_CONTROL" = false ] && [ -f "$LOCK_DIR_PATH/control.lck" ]; then
        ENABLE_CONTROL=true
    fi
fi

if [ -z "$PORT" ]; then
    PORT="$(arthexis_detect_backend_port "$SCRIPT_DIR")"
fi

if [ "$REPAIR" = true ] && [ -n "$SERVICE" ]; then
    stop_existing_units_for_repair "$SERVICE"
fi


BASE_DIR="$SCRIPT_DIR"
cd "$BASE_DIR"
DB_FILE="$BASE_DIR/db.sqlite3"

# Ensure the VERSION marker reflects the current revision before proceeding.
arthexis_update_version_marker "$BASE_DIR"
if [ -f "$DB_FILE" ]; then
    # Allow callers to purge or reuse an existing database depending on the mode requested.
    if [ "$CLEAN" = true ]; then
        BACKUP_DIR="$BASE_DIR/backups"
        mkdir -p "$BACKUP_DIR"
        VERSION="unknown"
        [ -f "$BASE_DIR/VERSION" ] && VERSION="$(cat "$BASE_DIR/VERSION")"
        REVISION="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
        STAMP="$(date +%Y%m%d%H%M%S)"
        cp "$DB_FILE" "$BACKUP_DIR/db.sqlite3.${VERSION}.${REVISION}.${STAMP}.bak"
        rm "$DB_FILE"
    elif [ "$REPAIR" = true ]; then
        echo "Repair mode: reusing existing database at $DB_FILE."
    else
        echo "Database file $DB_FILE exists. Use --clean to remove it before installing." >&2
        exit 1
    fi
fi
LOCK_DIR="$BASE_DIR/locks"
mkdir -p "$LOCK_DIR"
SYSTEMD_UNITS_LOCK="$LOCK_DIR/systemd_services.lck"

# Record role-specific prerequisites and capture supporting state for service management.
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


# Create virtual environment if missing
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi

echo "$PORT" > "$LOCK_DIR/backend_port.lck"
echo "$NGINX_MODE" > "$LOCK_DIR/nginx_mode.lck"
echo "$NODE_ROLE" > "$LOCK_DIR/role.lck"

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

# Apply database migrations for a ready-to-run schema.
python manage.py migrate --noinput

# Load personal user data fixtures if present
if ls data/*.json >/dev/null 2>&1; then
    python manage.py loaddata data/*.json
fi

# Refresh environment data and register this node
if [ "$CHANNEL" = "unstable" ]; then
    ./env-refresh.sh --latest
else
    ./env-refresh.sh
fi

deactivate


# If a service name was provided, install a systemd unit and persist its name
if [ -n "$SERVICE" ]; then
    SERVICE_FILE="/etc/systemd/system/${SERVICE}.service"
    echo "$SERVICE" > "$LOCK_DIR/service.lck"
    EXEC_CMD="$BASE_DIR/service-start.sh"
    sudo bash -c "cat > '$SERVICE_FILE'" <<SERVICEEOF
[Unit]
Description=Arthexis Constellation Django service
After=network.target
Wants=network.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
EnvironmentFile=-$BASE_DIR/redis.env
EnvironmentFile=-$BASE_DIR/debug.env
ExecStartPre=$BASE_DIR/scripts/prestart-refresh.sh
ExecStart=$EXEC_CMD
Restart=always
TimeoutStartSec=500
User=$(id -un)

[Install]
WantedBy=multi-user.target
SERVICEEOF
    arthexis_record_systemd_unit "$LOCK_DIR" "${SERVICE}.service"
    if [ "$ENABLE_CELERY" = true ]; then
        CELERY_SERVICE="celery-$SERVICE"
        CELERY_SERVICE_FILE="/etc/systemd/system/${CELERY_SERVICE}.service"
        sudo bash -c "cat > '$CELERY_SERVICE_FILE'" <<CELERYSERVICEEOF
[Unit]
Description=Celery Worker for $SERVICE
After=${SERVICE}.service network.target redis.service
Requires=${SERVICE}.service
PartOf=${SERVICE}.service

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
EnvironmentFile=-$BASE_DIR/redis.env
EnvironmentFile=-$BASE_DIR/debug.env
ExecStartPre=$BASE_DIR/scripts/prestart-refresh.sh
        ExecStart=$BASE_DIR/.venv/bin/celery -A config worker -l info --concurrency=1
        Restart=always
        TimeoutStartSec=500
        User=$(id -un)

[Install]
WantedBy=multi-user.target
CELERYSERVICEEOF
        arthexis_record_systemd_unit "$LOCK_DIR" "${CELERY_SERVICE}.service"
        CELERY_BEAT_SERVICE="celery-beat-$SERVICE"
        CELERY_BEAT_SERVICE_FILE="/etc/systemd/system/${CELERY_BEAT_SERVICE}.service"
        sudo bash -c "cat > '$CELERY_BEAT_SERVICE_FILE'" <<BEATSERVICEEOF
[Unit]
Description=Celery Beat for $SERVICE
After=${SERVICE}.service network.target redis.service
Requires=${SERVICE}.service
PartOf=${SERVICE}.service

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
EnvironmentFile=-$BASE_DIR/redis.env
EnvironmentFile=-$BASE_DIR/debug.env
ExecStartPre=$BASE_DIR/scripts/prestart-refresh.sh
ExecStart=$BASE_DIR/.venv/bin/celery -A config beat -l info
Restart=always
TimeoutStartSec=500
User=$(id -un)

[Install]
WantedBy=multi-user.target
BEATSERVICEEOF
        arthexis_record_systemd_unit "$LOCK_DIR" "${CELERY_BEAT_SERVICE}.service"
    fi
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE"
    if [ "$ENABLE_CELERY" = true ]; then
        sudo systemctl enable "$CELERY_SERVICE" "$CELERY_BEAT_SERVICE"
    fi
fi

if [ "$ENABLE_LCD_SCREEN" = true ] && [ -n "$SERVICE" ]; then
    LCD_SERVICE="lcd-$SERVICE"
    LCD_SERVICE_FILE="/etc/systemd/system/${LCD_SERVICE}.service"
    sudo bash -c "cat > '$LCD_SERVICE_FILE'" <<SERVICEEOF
[Unit]
Description=LCD screen updater service for Arthexis
After=${SERVICE}.service network.target
Requires=${SERVICE}.service
PartOf=${SERVICE}.service

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
ExecStart=$BASE_DIR/.venv/bin/python -m core.lcd_screen
Restart=always
TimeoutStartSec=500
StandardOutput=journal
StandardError=journal
User=$(id -un)

[Install]
WantedBy=multi-user.target
SERVICEEOF
    sudo systemctl daemon-reload
    sudo systemctl enable "$LCD_SERVICE"
    arthexis_record_systemd_unit "$LOCK_DIR" "${LCD_SERVICE}.service"
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
        arthexis_remove_systemd_unit_record "$LOCK_DIR" "${LCD_SERVICE}.service"
    fi
fi


if [ "$AUTO_UPGRADE" = true ]; then
    rm -f AUTO_UPGRADE
    echo "$CHANNEL" > "$LOCK_DIR/auto_upgrade.lck"
    if [ "$UPGRADE" = true ]; then
        if [ "$CHANNEL" = "unstable" ]; then
            ./upgrade.sh --latest
        else
            ./upgrade.sh --stable
        fi
    fi
    source .venv/bin/activate
    python manage.py shell <<'PYCODE'
from core.auto_upgrade import ensure_auto_upgrade_periodic_task

ensure_auto_upgrade_periodic_task()
PYCODE
    deactivate
elif [ "$UPGRADE" = true ]; then
    if [ "$CHANNEL" = "unstable" ]; then
        ./upgrade.sh --latest
    else
        ./upgrade.sh --stable
    fi
elif [ "$AUTO_UPGRADE" = false ]; then
    rm -f "$LOCK_DIR/auto_upgrade.lck"
elif [ -n "$SERVICE" ]; then
    sudo systemctl restart "$SERVICE"
    if [ "$ENABLE_CELERY" = true ]; then
        sudo systemctl restart "celery-$SERVICE"
        sudo systemctl restart "celery-beat-$SERVICE"
    fi
fi

if [ "$START_SERVICES" = true ]; then
    "$BASE_DIR/start.sh"
fi

arthexis_refresh_desktop_shortcuts "$BASE_DIR"

