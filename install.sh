#!/usr/bin/env bash
set -e

# Bootstrap logging and helper utilities used throughout the installation.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIP_INSTALL_HELPER="$SCRIPT_DIR/scripts/helpers/pip_install.py"
# shellcheck source=scripts/helpers/logging.sh
. "$SCRIPT_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/git_remote.sh
. "$SCRIPT_DIR/scripts/helpers/git_remote.sh"
# shellcheck source=scripts/helpers/desktop_shortcuts.sh
. "$SCRIPT_DIR/scripts/helpers/desktop_shortcuts.sh"
# shellcheck source=scripts/helpers/version_marker.sh
. "$SCRIPT_DIR/scripts/helpers/version_marker.sh"
# shellcheck source=scripts/helpers/ports.sh
. "$SCRIPT_DIR/scripts/helpers/ports.sh"
# shellcheck source=scripts/helpers/systemd_locks.sh
. "$SCRIPT_DIR/scripts/helpers/systemd_locks.sh"
# shellcheck source=scripts/helpers/service_manager.sh
. "$SCRIPT_DIR/scripts/helpers/service_manager.sh"
# shellcheck source=scripts/helpers/timing.sh
. "$SCRIPT_DIR/scripts/helpers/timing.sh"

# Determine the target user and re-exec as needed before continuing.
if [ -z "${ARTHEXIS_RUN_AS_USER:-}" ]; then
  TARGET_USER="$(arthexis_detect_service_user "$SCRIPT_DIR")"
  if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "root" ] && [ "$(id -un)" != "$TARGET_USER" ] && command -v sudo >/dev/null 2>&1 && sudo -n -u "$TARGET_USER" true >/dev/null 2>&1; then
    exec sudo -u "$TARGET_USER" ARTHEXIS_RUN_AS_USER="$TARGET_USER" "$SCRIPT_DIR/$(basename "$0")" "$@"
  fi
fi
arthexis_resolve_log_dir "$SCRIPT_DIR" LOG_DIR || exit 1
# Write a copy of stdout/stderr to a dedicated log file for troubleshooting.
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

# Default configuration flags populated by CLI parsing below.
ORIGINAL_ARGS=("$@")
SERVICE=""
PORT=""
DEFAULT_BASE_SERVICE_NAME="arthexis"
AUTO_UPGRADE=false
CHANNEL="stable"
UPGRADE=false
ENABLE_CELERY=false
SERVICE_MANAGEMENT_MODE="$ARTHEXIS_SERVICE_MODE_EMBEDDED"
SERVICE_MANAGEMENT_MODE_FLAG=false
START_FLAG=false
ENABLE_LCD_SCREEN=false
DISABLE_LCD_SCREEN=false
ENABLE_RFID_SERVICE=false
DISABLE_RFID_SERVICE=false
ENABLE_CAMERA_SERVICE=false
DISABLE_CAMERA_SERVICE=false
CLEAN=false
ENABLE_CONTROL=false
NODE_ROLE="Terminal"
REQUIRES_REDIS=false
START_SERVICES=false
REPAIR=false

usage() {
    echo "Usage: $0 [--service NAME] [--port PORT] [--upgrade] [--fixed] [--stable|--regular|--normal|--unstable|--latest] [--satellite] [--terminal] [--control] [--watchtower] [--celery] [--embedded|--systemd] [--lcd-screen|--no-lcd-screen] [--rfid-service|--no-rfid-service] [--camera-service|--no-camera-service] [--clean] [--start|--no-start] [--repair]" >&2
    exit 1
}

# Service management helpers to avoid lock conflicts during repair operations.
stop_existing_units_for_repair() {
    local service_name="$1"

    arthexis_stop_service_unit_stack "$service_name" "$ENABLE_CELERY" "$ENABLE_LCD_SCREEN" "$ENABLE_RFID_SERVICE" "$ENABLE_CAMERA_SERVICE"
}

clean_previous_installation_state() {
    local service_name="$1"
    local backup_dir="$BASE_DIR/backups"
    local work_dir="$BASE_DIR/work"
    local static_root="$BASE_DIR/static"
    local -a recorded_units=()

    mkdir -p "$LOCK_DIR"

    if [ -f "$SYSTEMD_UNITS_LOCK" ]; then
        mapfile -t recorded_units < "$SYSTEMD_UNITS_LOCK"
    fi

    if [ -z "$service_name" ] && [ -f "$LOCK_DIR/service.lck" ]; then
        service_name="$(cat "$LOCK_DIR/service.lck")"
    fi

    if [ -z "$service_name" ] && [ ${#recorded_units[@]} -gt 0 ]; then
        for unit in "${recorded_units[@]}"; do
            if [[ "$unit" == *.service ]]; then
                service_name="${unit%.service}"
                break
            fi
        done
    fi

    if [ -n "$service_name" ]; then
        arthexis_remove_service_unit_stack "$LOCK_DIR" "$service_name" true true true true
        arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${service_name}-upgrade-guard.service"
        arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${service_name}-upgrade-guard.timer"
    fi

    if [ ${#recorded_units[@]} -gt 0 ]; then
        for unit in "${recorded_units[@]}"; do
            arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "$unit"
        done
    fi

    if [ -d "$LOG_DIR" ]; then
        if [ -f "$LOG_FILE" ]; then
            find "$LOG_DIR" -type f ! -samefile "$LOG_FILE" -delete
            : > "$LOG_FILE"
        else
            find "$LOG_DIR" -type f -delete
        fi

        find "$LOG_DIR" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
        mkdir -p "$LOG_DIR/old"
        touch "$LOG_DIR/.gitkeep" "$LOG_DIR/old/.gitkeep"
    fi

    if [ -d "$work_dir" ]; then
        find "$work_dir" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
        touch "$work_dir/.gitkeep"
    fi

    if [ -d "$static_root" ]; then
        find "$static_root" -mindepth 1 -maxdepth 1 ! -name '.gitignore' -exec rm -rf {} +
    fi

    if [ -f "$DB_FILE" ]; then
        mkdir -p "$backup_dir"
        VERSION="unknown"
        [ -f "$BASE_DIR/VERSION" ] && VERSION="$(cat "$BASE_DIR/VERSION")"
        REVISION="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
        STAMP="$(date +%Y%m%d%H%M%S)"
        cp "$DB_FILE" "$backup_dir/db.sqlite3.${VERSION}.${REVISION}.${STAMP}.bak"
        rm "$DB_FILE"
    fi

    rm -f "$LOCK_DIR"/*.lck "$LOCK_DIR"/*.lock "$LOCK_DIR"/*.tmp "$LOCK_DIR"/service.lck
    rm -f "$SYSTEMD_UNITS_LOCK"
    rm -f "$LOCK_DIR/requirements.md5" \
          "$LOCK_DIR/requirements.sha256" \
          "$LOCK_DIR/migrations.md5" \
          "$LOCK_DIR/fixtures.md5" \
          "$BASE_DIR/redis.env" \
          "$BASE_DIR/debug.env"

    rm -rf "$LOCK_DIR"
}

reset_service_units_for_repair() {
    local service_name="$1"

    if [ -z "$service_name" ]; then
        return 0
    fi

    arthexis_remove_service_unit_stack "$LOCK_DIR" "$service_name" "$ENABLE_CELERY" "$ENABLE_LCD_SCREEN" "$ENABLE_RFID_SERVICE" "$ENABLE_CAMERA_SERVICE"

    if [ -f "$SYSTEMD_UNITS_LOCK" ]; then
        while IFS= read -r recorded_unit; do
            [ -z "$recorded_unit" ] && continue
            arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "$recorded_unit"
        done < "$SYSTEMD_UNITS_LOCK"
    fi
}

require_redis() {
    if ! command -v redis-cli >/dev/null 2>&1; then
        echo "Redis is required for the $1 role but is not installed."
        echo "Install redis-server and re-run this script. For Debian/Ubuntu:"
        echo "  sudo apt update && sudo apt install redis-server"
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

# Hardware support utilities.
ensure_i2c_packages() {
    if ! python3 -c 'import smbus' >/dev/null 2>&1 \
        && ! python3 -c 'import smbus2' >/dev/null 2>&1; then
        echo "smbus module not found. Installing i2c-tools and python3-smbus"
        sudo apt update
        sudo apt install -y i2c-tools python3-smbus
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
            AUTO_UPGRADE=true
            CHANNEL="unstable"
            shift
            ;;
        --stable|--regular|--normal)
            AUTO_UPGRADE=true
            CHANNEL="stable"
            shift
            ;;
        --celery)
            ENABLE_CELERY=true
            shift
            ;;
        --embedded)
            SERVICE_MANAGEMENT_MODE="$ARTHEXIS_SERVICE_MODE_EMBEDDED"
            SERVICE_MANAGEMENT_MODE_FLAG=true
            shift
            ;;
        --systemd)
            SERVICE_MANAGEMENT_MODE="$ARTHEXIS_SERVICE_MODE_SYSTEMD"
            SERVICE_MANAGEMENT_MODE_FLAG=true
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
        --rfid-service)
            ENABLE_RFID_SERVICE=true
            DISABLE_RFID_SERVICE=false
            shift
            ;;
        --no-rfid-service)
            ENABLE_RFID_SERVICE=false
            DISABLE_RFID_SERVICE=true
            shift
            ;;
        --camera-service)
            ENABLE_CAMERA_SERVICE=true
            DISABLE_CAMERA_SERVICE=false
            shift
            ;;
        --no-camera-service)
            ENABLE_CAMERA_SERVICE=false
            DISABLE_CAMERA_SERVICE=true
            shift
            ;;
        --clean)
            CLEAN=true
            shift
            ;;
        --start)
            START_SERVICES=true
            START_FLAG=true
            shift
            ;;
        --no-start)
            START_SERVICES=false
            START_FLAG=true
            shift
            ;;
        --repair)
            REPAIR=true
            shift
            ;;
        --satellite)
            SERVICE="arthexis"
            ENABLE_CELERY=true
            NODE_ROLE="Satellite"
            REQUIRES_REDIS=true
            shift
            ;;
        --terminal)
            SERVICE="arthexis"
            ENABLE_CELERY=true
            NODE_ROLE="Terminal"
            shift
            ;;
        --control)
            SERVICE="arthexis"
            ENABLE_CELERY=true
            ENABLE_LCD_SCREEN=true
            DISABLE_LCD_SCREEN=false
            ENABLE_CONTROL=true
            NODE_ROLE="Control"
            REQUIRES_REDIS=true
            if [ "$START_FLAG" = false ]; then
                START_SERVICES=true
            fi
            shift
            ;;
        --watchtower)
            SERVICE="arthexis"
            ENABLE_CELERY=true
            NODE_ROLE="Watchtower"
            REQUIRES_REDIS=true
            shift
            ;;
        *)
            usage
            ;;
    esac
done

if [ "$ENABLE_CAMERA_SERVICE" = true ]; then
    REQUIRES_REDIS=true
fi

if [ "$REPAIR" = true ]; then
    LOCK_DIR_PATH="$SCRIPT_DIR/.locks"
    if [ -z "$SERVICE" ] && [ -f "$LOCK_DIR_PATH/service.lck" ]; then
        SERVICE="$(cat "$LOCK_DIR_PATH/service.lck")"
        echo "Repair mode: reusing existing service '$SERVICE'."
    fi
    if [ -z "$SERVICE" ] && [ -f "$LOCK_DIR_PATH/systemd_services.lck" ]; then
        while IFS= read -r recorded_unit; do
            [ -z "$recorded_unit" ] && continue
            case "$recorded_unit" in
                *.service)
                    SERVICE="${recorded_unit%.service}"
                    echo "Repair mode: discovered service '$SERVICE' from recorded unit."
                    break
                    ;;
            esac
        done < "$LOCK_DIR_PATH/systemd_services.lck"
    fi
    if [ "$ENABLE_CELERY" = false ] && [ -f "$LOCK_DIR_PATH/celery.lck" ]; then
        ENABLE_CELERY=true
    fi
    if [ "$SERVICE_MANAGEMENT_MODE_FLAG" = false ]; then
        SERVICE_MANAGEMENT_MODE="$(arthexis_detect_service_mode "$LOCK_DIR_PATH")"
    fi
    if [ "$ENABLE_LCD_SCREEN" = false ] && arthexis_lcd_feature_enabled "$LOCK_DIR_PATH"; then
        ENABLE_LCD_SCREEN=true
        DISABLE_LCD_SCREEN=false
    fi
    if [ "$ENABLE_RFID_SERVICE" = false ] && [ -f "$LOCK_DIR_PATH/$ARTHEXIS_RFID_SERVICE_LOCK" ]; then
        ENABLE_RFID_SERVICE=true
        DISABLE_RFID_SERVICE=false
    fi
    if [ "$ENABLE_CAMERA_SERVICE" = false ] && [ -f "$LOCK_DIR_PATH/$ARTHEXIS_CAMERA_SERVICE_LOCK" ]; then
        ENABLE_CAMERA_SERVICE=true
        DISABLE_CAMERA_SERVICE=false
        REQUIRES_REDIS=true
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
LOCK_DIR="$BASE_DIR/.locks"
SYSTEMD_UNITS_LOCK="$LOCK_DIR/systemd_services.lck"
DB_FILE="$BASE_DIR/db.sqlite3"

arthexis_ensure_upstream_remotes "$BASE_DIR"


arthexis_timing_setup "install"

# Ensure the VERSION marker reflects the current revision before proceeding.
arthexis_update_version_marker "$BASE_DIR"
if [ "$CLEAN" = true ]; then
    clean_previous_installation_state "$SERVICE"
elif [ -f "$DB_FILE" ]; then
    # Allow callers to purge or reuse an existing database depending on the mode requested.
    if [ "$REPAIR" = true ]; then
        echo "Repair mode: reusing existing database at $DB_FILE."
    elif [ "$UPGRADE" = true ]; then
        echo "Upgrade mode: reusing existing database at $DB_FILE."
    else
        echo "Database file $DB_FILE exists. Use --clean to remove it before installing." >&2
        exit 1
    fi
fi
mkdir -p "$LOCK_DIR"
arthexis_record_service_mode "$LOCK_DIR" "$SERVICE_MANAGEMENT_MODE"

if [ "$REPAIR" = true ] && [ -n "$SERVICE" ]; then
    reset_service_units_for_repair "$SERVICE"
fi

if [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_EMBEDDED" ]; then
    if [ -n "$SERVICE" ]; then
        arthexis_remove_celery_unit_stack "$LOCK_DIR" "$SERVICE"
    fi
    if [ -f "$LOCK_DIR/service.lck" ] && [ -z "$SERVICE" ]; then
        EXISTING_SERVICE="$(cat "$LOCK_DIR/service.lck")"
        if [ -n "$EXISTING_SERVICE" ]; then
            arthexis_remove_celery_unit_stack "$LOCK_DIR" "$EXISTING_SERVICE"
        fi
    fi
    if [ -f "$SYSTEMD_UNITS_LOCK" ]; then
        while IFS= read -r recorded_unit; do
            case "$recorded_unit" in
                celery-*.service|celery-beat-*.service)
                    arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "$recorded_unit"
                    ;;
            esac
        done < "$SYSTEMD_UNITS_LOCK"
    fi
fi

# Record role-specific prerequisites and capture supporting state for service management.
if [ "$REQUIRES_REDIS" = true ]; then
    require_redis "$NODE_ROLE"
fi

if [ "$ENABLE_CELERY" = true ]; then
    touch "$LOCK_DIR/celery.lck"
else
    rm -f "$LOCK_DIR/celery.lck"
fi

LCD_LOCK="$LOCK_DIR/$ARTHEXIS_LCD_LOCK"
if [ "$ENABLE_LCD_SCREEN" = true ]; then
    touch "$LCD_LOCK"
    arthexis_enable_lcd_feature_flag "$LOCK_DIR"
    ensure_i2c_packages
else
    rm -f "$LCD_LOCK"
    arthexis_disable_lcd_feature_flag "$LOCK_DIR"
fi

RFID_SERVICE_LOCK="$LOCK_DIR/$ARTHEXIS_RFID_SERVICE_LOCK"
if [ "$ENABLE_RFID_SERVICE" = true ]; then
    touch "$RFID_SERVICE_LOCK"
elif [ "$DISABLE_RFID_SERVICE" = true ]; then
    rm -f "$RFID_SERVICE_LOCK"
fi

CAMERA_SERVICE_LOCK="$LOCK_DIR/$ARTHEXIS_CAMERA_SERVICE_LOCK"
if [ "$ENABLE_CAMERA_SERVICE" = true ]; then
    touch "$CAMERA_SERVICE_LOCK"
elif [ "$DISABLE_CAMERA_SERVICE" = true ]; then
    rm -f "$CAMERA_SERVICE_LOCK"
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


collect_requirement_files() {
    local -n out_array="$1"

    mapfile -t out_array < <(find "$BASE_DIR" -maxdepth 1 -type f -name 'requirements*.txt' -print | sort)
}

compute_requirements_checksum() {
    local -a files=("$@")

    if [ ${#files[@]} -eq 0 ]; then
        echo ""
        return 0
    fi

    (
        for file in "${files[@]}"; do
            printf '%s\n' "${file##*/}"
            cat "$file"
        done
    ) | sha256sum | awk '{print $1}'
}

arthexis_timing_start "virtualenv_setup"
# Create virtual environment if missing
NEW_VENV=false
if [ ! -d .venv ]; then
    if ! python3 -m venv .venv; then
        echo "Failed to create virtual environment. Ensure the python3-venv package is installed (e.g. sudo apt install python3-venv)." >&2
        exit 1
    fi
    NEW_VENV=true
    arthexis_timing_end "virtualenv_setup" "created"
else
    arthexis_timing_end "virtualenv_setup" "existing"
fi

if [ ! -f .venv/bin/activate ]; then
    echo "Virtual environment activation script not found at .venv/bin/activate. Attempting to recreate the virtual environment." >&2
    rm -rf .venv
    if ! python3 -m venv .venv; then
        echo "Failed to recreate virtual environment. Ensure the python3-venv package is installed (e.g. sudo apt install python3-venv)." >&2
        exit 1
    fi
    NEW_VENV=true
fi

if [ ! -f .venv/bin/activate ]; then
    echo "Virtual environment activation script not found at .venv/bin/activate after recreation. The .venv directory may be corrupted. On Debian/Ubuntu, you may need to install the 'python3-venv' package." >&2
    exit 1
fi

echo "$PORT" > "$LOCK_DIR/backend_port.lck"
echo "$NODE_ROLE" > "$LOCK_DIR/role.lck"

source .venv/bin/activate
arthexis_timing_start "pip_bootstrap"
REQ_HASH_FILE="$LOCK_DIR/requirements.bundle.sha256"
PIP_VERSION_MARKER="$LOCK_DIR/pip.version"
STORED_REQ_HASH=""
if [ -f "$REQ_HASH_FILE" ]; then
    STORED_REQ_HASH="$(cat "$REQ_HASH_FILE")"
fi
REQUIREMENT_FILES=()
collect_requirement_files REQUIREMENT_FILES
CURRENT_REQ_HASH="$(compute_requirements_checksum "${REQUIREMENT_FILES[@]}")"

PIP_UPGRADE=false
if [ "$NEW_VENV" = true ] || [ "$CLEAN" = true ]; then
    PIP_UPGRADE=true
elif [ -n "$CURRENT_REQ_HASH" ] && [ "$CURRENT_REQ_HASH" != "$STORED_REQ_HASH" ]; then
    PIP_UPGRADE=true
    CURRENT_PIP_VERSION="$(python -c 'import pip; print(pip.__version__)' 2>/dev/null)"
    if [ -n "$CURRENT_PIP_VERSION" ] && [ -f "$PIP_VERSION_MARKER" ]; then
        STORED_PIP_VERSION="$(cat "$PIP_VERSION_MARKER")"
        if [ "$CURRENT_PIP_VERSION" = "$STORED_PIP_VERSION" ]; then
            PIP_UPGRADE=false
        fi
    fi
fi

if [ "$PIP_UPGRADE" = true ]; then
    pip install --upgrade pip
    python -c 'import pip; print(pip.__version__)' 2>/dev/null > "$PIP_VERSION_MARKER" || true
    arthexis_timing_end "pip_bootstrap"
else
    arthexis_timing_record "pip_bootstrap" 0 "skipped"
fi

arthexis_timing_start "requirements_install"
env_refresh_args=(--force-refresh --deps-only)
if [ "$CHANNEL" = "unstable" ]; then
    env_refresh_args+=(--latest)
fi
INSTALL_HARDWARE_DEPS=false
if [ "$ENABLE_CONTROL" = true ] || [ "$ENABLE_RFID_SERVICE" = true ] || [ "$ENABLE_LCD_SCREEN" = true ]; then
    INSTALL_HARDWARE_DEPS=true
fi
run_env_refresh() {
    local -a env_prefix=()
    if [ "$INSTALL_HARDWARE_DEPS" = true ]; then
        env_prefix=(env ARTHEXIS_INSTALL_HARDWARE_DEPS=1)
    fi
    "${env_prefix[@]}" ./env-refresh.sh "$@"
}

run_env_refresh "${env_refresh_args[@]}"
arthexis_timing_end "requirements_install" "refreshed"


if [ "$ENABLE_CONTROL" = true ]; then
    echo "Checking for RFID scanner hardware..."
if python -m apps.cards.detect; then
        touch "$RFID_LOCK"
        echo "Enabled node feature 'rfid-scanner' based on detected hardware."
    else
        rm -f "$RFID_LOCK"
        echo "Skipped enabling 'rfid-scanner'; hardware not detected during install."
    fi
fi

# Apply database migrations for a ready-to-run schema.
arthexis_timing_start "django_migrate"
run_migration=false
if ! python manage.py migrate --check; then
    if migration_plan=$(python manage.py showmigrations --plan); then
        if grep -q '^[[:space:]]*\\[ \\]' <<< "$migration_plan"; then
            run_migration=true
        fi
    else
        echo "Failed to inspect migrations" >&2
        exit 1
    fi
fi

if [ "$run_migration" = true ]; then
    python manage.py migrate --noinput
    arthexis_timing_end "django_migrate"
else
    arthexis_timing_record "django_migrate" 0 "skipped"
fi

# Load personal user data fixtures if present
if ls data/*.json >/dev/null 2>&1; then
    arthexis_timing_start "load_user_data"
    python manage.py load_user_data data/*.json
    arthexis_timing_end "load_user_data"
else
    arthexis_timing_record "load_user_data" 0 "skipped"
fi

# Refresh environment data and register this node
arthexis_timing_start "env_refresh"
if [ "$CHANNEL" = "unstable" ]; then
    run_env_refresh --latest
else
    run_env_refresh
fi
arthexis_timing_end "env_refresh"

deactivate


# If a service name was provided, install a systemd unit and persist its name
if [ -n "$SERVICE" ]; then
    echo "$SERVICE" > "$LOCK_DIR/service.lck"
    if [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
        arthexis_record_systemd_unit "$LOCK_DIR" "${SERVICE}.service"
    fi
    EXEC_CMD="$BASE_DIR/scripts/service-start.sh"
    arthexis_install_service_stack "$BASE_DIR" "$LOCK_DIR" "$SERVICE" "$ENABLE_CELERY" "$EXEC_CMD" "$SERVICE_MANAGEMENT_MODE"
fi

if [ -n "$SERVICE" ]; then
    LCD_SERVICE="lcd-$SERVICE"
    RFID_SERVICE="rfid-$SERVICE"
    CAMERA_SERVICE="camera-$SERVICE"
    if [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
        if [ "$DISABLE_LCD_SCREEN" = true ]; then
            if systemctl list-unit-files | grep -Fq "${LCD_SERVICE}.service"; then
                sudo systemctl stop "$LCD_SERVICE" || true
                sudo systemctl disable "$LCD_SERVICE" || true
                LCD_SERVICE_FILE="/etc/systemd/system/$(basename "${LCD_SERVICE}").service"
                if [ -f "$LCD_SERVICE_FILE" ]; then
                    sudo rm "$LCD_SERVICE_FILE"
                fi
                sudo systemctl daemon-reload
            fi
            arthexis_remove_systemd_unit_record "$LOCK_DIR" "${LCD_SERVICE}.service"
        elif [ "$ENABLE_LCD_SCREEN" = true ] || [ "$ENABLE_CONTROL" = true ]; then
            arthexis_install_lcd_service_unit "$BASE_DIR" "$LOCK_DIR" "$SERVICE"
        else
            arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${LCD_SERVICE}.service"
        fi

        if [ "$DISABLE_RFID_SERVICE" = true ]; then
            if systemctl list-unit-files | grep -Fq "${RFID_SERVICE}.service"; then
                sudo systemctl stop "$RFID_SERVICE" || true
                sudo systemctl disable "$RFID_SERVICE" || true
                RFID_SERVICE_FILE="/etc/systemd/system/$(basename "${RFID_SERVICE}").service"
                if [ -f "$RFID_SERVICE_FILE" ]; then
                    sudo rm "$RFID_SERVICE_FILE"
                fi
                sudo systemctl daemon-reload
            fi
            arthexis_remove_systemd_unit_record "$LOCK_DIR" "${RFID_SERVICE}.service"
        elif [ "$ENABLE_RFID_SERVICE" = true ]; then
            arthexis_install_rfid_service_unit "$BASE_DIR" "$LOCK_DIR" "$SERVICE"
        else
            arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${RFID_SERVICE}.service"
        fi

        if [ "$DISABLE_CAMERA_SERVICE" = true ]; then
            if systemctl list-unit-files | grep -Fq "${CAMERA_SERVICE}.service"; then
                sudo systemctl stop "$CAMERA_SERVICE" || true
                sudo systemctl disable "$CAMERA_SERVICE" || true
                CAMERA_SERVICE_FILE="/etc/systemd/system/$(basename "${CAMERA_SERVICE}").service"
                if [ -f "$CAMERA_SERVICE_FILE" ]; then
                    sudo rm "$CAMERA_SERVICE_FILE"
                fi
                sudo systemctl daemon-reload
            fi
            arthexis_remove_systemd_unit_record "$LOCK_DIR" "${CAMERA_SERVICE}.service"
        elif [ "$ENABLE_CAMERA_SERVICE" = true ]; then
            arthexis_install_camera_service_unit "$BASE_DIR" "$LOCK_DIR" "$SERVICE"
        else
            arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${CAMERA_SERVICE}.service"
        fi
    else
        arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${LCD_SERVICE}.service"
        arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${RFID_SERVICE}.service"
        arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${CAMERA_SERVICE}.service"
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
from apps.core.auto_upgrade import ensure_auto_upgrade_periodic_task

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
    if [ "$ENABLE_CELERY" = true ] && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
        sudo systemctl restart "celery-$SERVICE"
        sudo systemctl restart "celery-beat-$SERVICE"
    fi
fi

if [ "$START_SERVICES" = true ]; then
    "$BASE_DIR/start.sh"
fi

arthexis_refresh_desktop_shortcuts "$BASE_DIR"
