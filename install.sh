#!/usr/bin/env bash
set -e

# Bootstrap logging and helper utilities used throughout the installation.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIP_INSTALL_HELPER="$SCRIPT_DIR/scripts/helpers/pip_install.py"
# shellcheck source=scripts/helpers/common.sh
. "$SCRIPT_DIR/scripts/helpers/common.sh"
# shellcheck source=scripts/helpers/logging.sh
. "$SCRIPT_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/git_remote.sh
. "$SCRIPT_DIR/scripts/helpers/git_remote.sh"
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
    exec sudo -u "$TARGET_USER" \
      ARTHEXIS_RUN_AS_USER="$TARGET_USER" \
      ARTHEXIS_MIGRATION_POLICY="${ARTHEXIS_MIGRATION_POLICY:-}" \
      "$SCRIPT_DIR/$(basename "$0")" "$@"
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
CELERY_MODE=""
SERVICE_MANAGEMENT_MODE=""
SERVICE_MANAGEMENT_MODE_FLAG=false
START_FLAG=false
ENABLE_LCD_SCREEN=false
DISABLE_LCD_SCREEN=false
ENABLE_RFID_SERVICE=false
DISABLE_RFID_SERVICE=false
ENABLE_CAMERA_SERVICE=false
DISABLE_CAMERA_SERVICE=false
ENABLE_IMAGER_BURNER_SERVICE=false
DISABLE_IMAGER_BURNER_SERVICE=false
ENABLE_BOOT_UPGRADE=false
DISABLE_BOOT_UPGRADE=false
ENABLE_CHARGER_FACING=false
ENABLE_OCPP_GATEWAY=false
DISABLE_CHARGER_FACING=false
CHARGER_FACING_ROUTE_EXPLICIT=false
CLEAN=false
ENABLE_CONTROL=false
NODE_ROLE="Terminal"
NODE_ROLE_EXPLICIT=false
REQUIRES_REDIS=false
START_SERVICES=false
REPAIR=false

is_debian_host() {
    if [ ! -f /etc/os-release ]; then
        return 1
    fi

    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}" in
        debian)
            return 0
            ;;
    esac

    case " ${ID_LIKE:-} " in
        *" debian "*)
            return 0
            ;;
    esac

    return 1
}

# Lifecycle CLI contract: keep this usage block aligned with docs/development/install-lifecycle-scripts-manual.md and lifecycle contract tests.
usage() {
    echo "Usage: $0 [--service NAME] [--port PORT] [--upgrade] [--fixed] [--stable|--lts|--regular|--normal|--unstable|--latest] [--satellite] [--terminal] [--control] [--watchtower] [--celery|--no-celery] [--embedded|--systemd] [--lcd-screen|--no-lcd-screen] [--rfid-service|--no-rfid-service] [--camera-service|--no-camera-service] [--imager-burner-service|--no-imager-burner-service] [--boot-upgrade|--no-boot-upgrade] [--charger-facing|--ocpp-gateway|--no-charger-facing] [--clean] [--start|--no-start] [--repair]" >&2
    exit 1
}

# Service management helpers to avoid lock conflicts during repair operations.
stop_existing_units_for_repair() {
    local service_name="$1"

    arthexis_stop_service_unit_stack "$service_name" "$ENABLE_CELERY" "$ENABLE_LCD_SCREEN" "$ENABLE_RFID_SERVICE" "$ENABLE_CAMERA_SERVICE" "$ENABLE_IMAGER_BURNER_SERVICE"
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
          "$BASE_DIR/collectstatic.env" \
          "$BASE_DIR/redis.env" \
          "$BASE_DIR/debug.env" \
          "$BASE_DIR/migration.env"

    rm -rf "$LOCK_DIR"
}

reset_service_units_for_repair() {
    local service_name="$1"

    if [ -z "$service_name" ]; then
        return 0
    fi

    arthexis_remove_service_unit_stack "$LOCK_DIR" "$service_name" "$ENABLE_CELERY" "$ENABLE_LCD_SCREEN" "$ENABLE_RFID_SERVICE" "$ENABLE_CAMERA_SERVICE" "$ENABLE_IMAGER_BURNER_SERVICE"

    if [ -f "$SYSTEMD_UNITS_LOCK" ]; then
        while IFS= read -r recorded_unit; do
            [ -z "$recorded_unit" ] && continue
            arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "$recorded_unit"
        done < "$SYSTEMD_UNITS_LOCK"
    fi
}

extract_redis_db_from_url() {
    local redis_url="$1"

    if [[ "$redis_url" =~ /([0-9]+)$ ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
    fi
}

is_valid_redis_db() {
    local value="$1"

    [[ "$value" =~ ^[0-9]+$ ]]
}

read_env_value() {
    local env_path="$1"
    local key="$2"

    awk -F= -v target="$key" '
        BEGIN {
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", target)
        }
        {
            raw_key = $1
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", raw_key)
            if (raw_key == target) {
                value = substr($0, index($0, "=") + 1)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
                gsub(/^"|"$/, "", value)
                gsub(/^'\''|'\''$/, "", value)
                found = 1
            }
        }
        END {
            if (found) {
                print value
            }
        }
    ' "$env_path"
}

write_redis_env() {
    local role="$1"
    local redis_host="${REDIS_HOST:-localhost}"
    local redis_port="${REDIS_PORT:-6379}"
    local redis_base="redis://${redis_host}:${redis_port}"
    local redis_db="${REDIS_DB:-0}"
    local celery_redis_db="${CELERY_REDIS_DB:-$redis_db}"
    local channel_redis_db="${CHANNEL_REDIS_DB:-$redis_db}"
    local ocpp_state_redis_db="${OCPP_STATE_REDIS_DB:-$redis_db}"
    local existing_redis_env="$BASE_DIR/redis.env"
    local extracted_redis_db=""

    if [ -f "$existing_redis_env" ]; then
        local celery_broker_url=""
        local channel_redis_url=""
        local ocpp_state_redis_url=""

        celery_broker_url="$(read_env_value "$existing_redis_env" "CELERY_BROKER_URL")"
        channel_redis_url="$(read_env_value "$existing_redis_env" "CHANNEL_REDIS_URL")"
        ocpp_state_redis_url="$(read_env_value "$existing_redis_env" "OCPP_STATE_REDIS_URL")"

        if [ -n "$celery_broker_url" ] && [ -z "${CELERY_REDIS_DB:-}" ] && [ -z "${REDIS_DB:-}" ]; then
            extracted_redis_db="$(extract_redis_db_from_url "$celery_broker_url")"
            if [ -n "$extracted_redis_db" ]; then
                celery_redis_db="$extracted_redis_db"
            fi
        fi
        if [ -n "$channel_redis_url" ] && [ -z "${CHANNEL_REDIS_DB:-}" ] && [ -z "${REDIS_DB:-}" ]; then
            extracted_redis_db="$(extract_redis_db_from_url "$channel_redis_url")"
            if [ -n "$extracted_redis_db" ]; then
                channel_redis_db="$extracted_redis_db"
            fi
        fi
        if [ -n "$ocpp_state_redis_url" ] && [ -z "${OCPP_STATE_REDIS_DB:-}" ] && [ -z "${REDIS_DB:-}" ]; then
            extracted_redis_db="$(extract_redis_db_from_url "$ocpp_state_redis_url")"
            if [ -n "$extracted_redis_db" ]; then
                ocpp_state_redis_db="$extracted_redis_db"
            fi
        fi
    fi

    for redis_db_value in "$redis_db" "$celery_redis_db" "$channel_redis_db" "$ocpp_state_redis_db"; do
        if ! is_valid_redis_db "$redis_db_value"; then
            echo "Redis DB values must be non-negative integers." >&2
            exit 1
        fi
    done

    cat > "$BASE_DIR/redis.env" <<EOF
CELERY_BROKER_URL=${redis_base}/${celery_redis_db}
CELERY_RESULT_BACKEND=${redis_base}/${celery_redis_db}
EOF

    case "${role,,}" in
        satellite)
            cat >> "$BASE_DIR/redis.env" <<EOF
OCPP_STATE_REDIS_URL=${redis_base}/${ocpp_state_redis_db}
EOF
            ;;
        watchtower)
            cat >> "$BASE_DIR/redis.env" <<EOF
CHANNEL_REDIS_URL=${redis_base}/${channel_redis_db}
EOF
            ;;
    esac
}

require_redis() {
    local redis_host="${REDIS_HOST:-localhost}"
    local redis_port="${REDIS_PORT:-6379}"

    if ! command -v redis-cli >/dev/null 2>&1; then
        echo "Redis is required for the $1 role but is not installed."
        echo "Install redis-server and re-run this script. For Debian/Ubuntu:"
        echo "  sudo apt update && sudo apt install redis-server"
        exit 1
    fi
    if ! redis-cli -h "$redis_host" -p "$redis_port" ping >/dev/null 2>&1; then
        echo "Redis is required for the $1 role but does not appear to be running."
        echo "Checked Redis at ${redis_host}:${redis_port}."
        echo "Start redis and re-run this script. For Debian/Ubuntu:"
        echo "  sudo systemctl start redis-server"
        exit 1
    fi

    write_redis_env "$1"
}

# Hardware support utilities.
ensure_i2c_packages() {
    local python_bin="$1"

    if ! "$python_bin" -c 'import smbus' >/dev/null 2>&1 \
        && ! "$python_bin" -c 'import smbus2' >/dev/null 2>&1; then
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
            CHANNEL="latest"
            shift
            ;;
        --stable|--lts)
            AUTO_UPGRADE=true
            CHANNEL="stable"
            shift
            ;;
        --regular|--normal)
            AUTO_UPGRADE=true
            CHANNEL="regular"
            shift
            ;;
        --celery)
            if [ "$CELERY_MODE" = "disable" ]; then
                echo "Cannot combine --celery with --no-celery" >&2
                usage
            fi
            CELERY_MODE="enable"
            ENABLE_CELERY=true
            shift
            ;;
        --no-celery)
            if [ "$CELERY_MODE" = "enable" ]; then
                echo "Cannot combine --celery with --no-celery" >&2
                usage
            fi
            CELERY_MODE="disable"
            ENABLE_CELERY=false
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
            echo "LCD service installation moved to gway-lcd-sound." >&2
            exit 2
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
        --imager-burner-service)
            ENABLE_IMAGER_BURNER_SERVICE=true
            DISABLE_IMAGER_BURNER_SERVICE=false
            shift
            ;;
        --no-imager-burner-service)
            ENABLE_IMAGER_BURNER_SERVICE=false
            DISABLE_IMAGER_BURNER_SERVICE=true
            shift
            ;;
        --boot-upgrade)
            ENABLE_BOOT_UPGRADE=true
            DISABLE_BOOT_UPGRADE=false
            shift
            ;;
        --no-boot-upgrade)
            ENABLE_BOOT_UPGRADE=false
            DISABLE_BOOT_UPGRADE=true
            shift
            ;;
        --charger-facing)
            ENABLE_CHARGER_FACING=true
            DISABLE_CHARGER_FACING=false
            CHARGER_FACING_ROUTE_EXPLICIT=true
            shift
            ;;
        --ocpp-gateway)
            ENABLE_OCPP_GATEWAY=true
            DISABLE_CHARGER_FACING=false
            CHARGER_FACING_ROUTE_EXPLICIT=true
            shift
            ;;
        --no-charger-facing|--no-ocpp-gateway)
            ENABLE_CHARGER_FACING=false
            ENABLE_OCPP_GATEWAY=false
            DISABLE_CHARGER_FACING=true
            CHARGER_FACING_ROUTE_EXPLICIT=true
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
            NODE_ROLE_EXPLICIT=true
            REQUIRES_REDIS=true
            shift
            ;;
        --terminal)
            SERVICE="arthexis"
            ENABLE_CELERY=true
            NODE_ROLE="Terminal"
            NODE_ROLE_EXPLICIT=true
            shift
            ;;
        --control)
            SERVICE="arthexis"
            ENABLE_CELERY=true
            if [ "$DISABLE_IMAGER_BURNER_SERVICE" = false ] && command -v lsblk >/dev/null 2>&1; then
                ENABLE_IMAGER_BURNER_SERVICE=true
            fi
            ENABLE_CONTROL=true
            NODE_ROLE="Control"
            NODE_ROLE_EXPLICIT=true
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
            NODE_ROLE_EXPLICIT=true
            REQUIRES_REDIS=true
            shift
            ;;
        *)
            usage
            ;;
    esac
done

if [ ${#ORIGINAL_ARGS[@]} -eq 0 ] && is_debian_host; then
    SERVICE="arthexis"
    ENABLE_CELERY=true
    REQUIRES_REDIS=true
fi

apply_node_role_runtime_defaults() {
    case "${NODE_ROLE,,}" in
        satellite|control|watchtower)
            REQUIRES_REDIS=true
            ;;
    esac

    case "${NODE_ROLE,,}" in
        control)
            ENABLE_CONTROL=true
            ;;
    esac
}

restore_node_role_from_lock_for_repair() {
    local role_lock_path="$1"
    local restored_role=""

    if [[ "$NODE_ROLE_EXPLICIT" == false && -f "$role_lock_path" ]]; then
        restored_role="$(tr -d '[:space:]' < "$role_lock_path")"
        case "${restored_role,,}" in
            terminal|satellite|control|watchtower)
                NODE_ROLE="$restored_role"
                apply_node_role_runtime_defaults
                ;;
        esac
    fi
}

write_role_enabled_apps_lock() {
    local role="$1"
    local mode="${2:-preserve}"
    local lock_path="$LOCK_DIR/enabled_apps.lck"

    if [ -f "$lock_path" ] && [ "$mode" != "refresh" ]; then
        echo "Enabled-apps lock already present; preserving $lock_path."
        return 0
    fi

    arthexis_timing_start "enabled_apps_lock"
    if "$PYTHON_BOOTSTRAP_BIN" - "$BASE_DIR" "$role" <<'PY'
import os
import re
import sys
from pathlib import Path

base_dir = Path(sys.argv[1])
role = sys.argv[2]
sys.path.insert(0, str(base_dir))

from utils.enabled_apps_lock import write_enabled_apps_lock
from utils.role_app_profiles import (
    explain_role_app_selectors,
    get_direct_lock_app_selectors,
)


def split_env(*names):
    values = []
    for name in names:
        raw_value = os.environ.get(name, "")
        values.extend(part for part in re.split(r"[,;\s]+", raw_value) if part)
    return tuple(dict.fromkeys(values))


def charger_facing_routes_enabled(base_dir):
    lock_dir = base_dir / ".locks"
    return (lock_dir / "charger_facing.lck").exists() or (
        lock_dir / "ocpp_gateway.lck"
    ).exists()


def direct_result_source_for_reasons(reasons):
    if "explicit-include" in reasons:
        return None
    for reason in reasons:
        if reason.startswith("feature-pack:"):
            return reason
    for reason in reasons:
        if reason.startswith(("role-default:", "full-app-fallback:")):
            return reason
    return None


def direct_result_sources(result):
    direct_selectors = set(get_direct_lock_app_selectors(result))
    sources = {}
    for item in result.explanations:
        if item.selector not in direct_selectors:
            continue
        source = direct_result_source_for_reasons(item.reasons)
        if source:
            sources[item.selector] = source
    return sources


result = explain_role_app_selectors(
    role,
    feature_packs=split_env("ARTHEXIS_ROLE_APP_FEATURE_PACKS", "ARTHEXIS_FEATURE_PACKS"),
    disabled_apps=split_env("ARTHEXIS_ROLE_APP_DISABLED_APPS", "ARTHEXIS_DISABLED_APPS"),
)
direct_selectors = get_direct_lock_app_selectors(result)
direct_sources = direct_result_sources(result)
if charger_facing_routes_enabled(base_dir) and "apps.ocpp" not in direct_selectors:
    direct_selectors = (*direct_selectors, "apps.ocpp")
    direct_sources["apps.ocpp"] = "charger-facing"
lock_path = write_enabled_apps_lock(
    result.selectors,
    base_dir,
    direct_apps=direct_selectors,
    direct_app_sources=direct_sources,
)
print(f"Wrote enabled-apps lock for {role}: {lock_path} ({len(result.selectors)} apps).")
PY
    then
        arthexis_timing_end "enabled_apps_lock"
    else
        status=$?
        arthexis_timing_end "enabled_apps_lock" "failed"
        return "$status"
    fi
}

refresh_charger_facing_route_lock_metadata() {
    local lock_path="$LOCK_DIR/enabled_apps.lck"

    if [[ ! -f "$lock_path" ]]; then
        echo "No enabled-apps lock present; preserving full app fallback while route locks drive runtime binding."
        return 0
    fi

    arthexis_timing_start "enabled_apps_route_metadata"
    if "$PYTHON_BOOTSTRAP_BIN" - "$BASE_DIR" <<'PY'
import sys
from pathlib import Path

base_dir = Path(sys.argv[1])
sys.path.insert(0, str(base_dir))

from utils.enabled_apps_lock import (
    read_enabled_apps_lock,
    read_enabled_apps_lock_direct_entries,
    read_enabled_apps_lock_direct_sources,
    write_enabled_apps_lock,
)


def charger_facing_routes_enabled(base_dir):
    lock_dir = base_dir / ".locks"
    return (lock_dir / "charger_facing.lck").exists() or (
        lock_dir / "ocpp_gateway.lck"
    ).exists()


enabled_entries = read_enabled_apps_lock(base_dir)
if enabled_entries is None:
    print("No enabled-apps lock present; route metadata unchanged.")
    raise SystemExit(0)

direct_entries = read_enabled_apps_lock_direct_entries(base_dir)
direct_sources = read_enabled_apps_lock_direct_sources(base_dir)
next_direct_entries = set(direct_entries or ())
if charger_facing_routes_enabled(base_dir):
    if "apps.ocpp" not in next_direct_entries:
        direct_sources["apps.ocpp"] = "charger-facing"
    next_direct_entries.add("apps.ocpp")
elif direct_sources.get("apps.ocpp") == "charger-facing":
    next_direct_entries.discard("apps.ocpp")
    direct_sources.pop("apps.ocpp", None)

direct_apps = (
    tuple(sorted(next_direct_entries))
    if direct_entries is not None or next_direct_entries
    else None
)
next_direct_sources = {
    selector: source
    for selector, source in direct_sources.items()
    if selector in next_direct_entries
}
lock_path = write_enabled_apps_lock(
    enabled_entries,
    base_dir,
    direct_apps=direct_apps,
    direct_app_sources=next_direct_sources,
)
print(f"Updated charger-facing route metadata in enabled-apps lock: {lock_path}.")
PY
    then
        arthexis_timing_end "enabled_apps_route_metadata"
    else
        status=$?
        arthexis_timing_end "enabled_apps_route_metadata" "failed"
        return "$status"
    fi
}

role_app_profiles_explicitly_enabled() {
    case "${ARTHEXIS_ROLE_APP_PROFILES:-}" in
        1|true|TRUE|True|yes|YES|Yes|on|ON|On)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

role_app_profile_inputs_present() {
    [[ -n "${ARTHEXIS_ROLE_APP_FEATURE_PACKS:-}" ]] && return 0
    [[ -n "${ARTHEXIS_FEATURE_PACKS:-}" ]] && return 0
    [[ -n "${ARTHEXIS_ROLE_APP_DISABLED_APPS:-}" ]] && return 0
    [[ -n "${ARTHEXIS_DISABLED_APPS:-}" ]] && return 0
    return 1
}

charger_facing_route_refresh_required() {
    [[ "$CHARGER_FACING_ROUTE_EXPLICIT" == true ]] && return 0
    [[ "$ENABLE_CHARGER_FACING" == true || "$ENABLE_OCPP_GATEWAY" == true ]]
}

if [ "$REPAIR" = true ]; then
    restore_node_role_from_lock_for_repair "$SCRIPT_DIR/.locks/role.lck"
fi

if [ "$CELERY_MODE" = "enable" ]; then
    ENABLE_CELERY=true
elif [ "$CELERY_MODE" = "disable" ]; then
    ENABLE_CELERY=false
fi

if [ "$ENABLE_CAMERA_SERVICE" = true ]; then
    REQUIRES_REDIS=true
fi

if [ "$SERVICE_MANAGEMENT_MODE_FLAG" = false ]; then
    case "${NODE_ROLE,,}" in
        satellite|watchtower)
            SERVICE_MANAGEMENT_MODE="$ARTHEXIS_SERVICE_MODE_SYSTEMD"
            ;;
        *)
            SERVICE_MANAGEMENT_MODE="$ARTHEXIS_SERVICE_MODE_EMBEDDED"
            ;;
    esac
fi

apply_role_charger_facing_default() {
    case "${NODE_ROLE,,}" in
        satellite|control)
            if [[ "$ENABLE_CHARGER_FACING" == false && "$ENABLE_OCPP_GATEWAY" == false && "$DISABLE_CHARGER_FACING" == false ]]; then
                ENABLE_CHARGER_FACING=true
            fi
            ;;
    esac
}

LOCK_DIR_PATH="$SCRIPT_DIR/.locks"
if [[ "$CLEAN" == false && "$ENABLE_CHARGER_FACING" == false && "$ENABLE_OCPP_GATEWAY" == false && "$DISABLE_CHARGER_FACING" == false ]]; then
    if [[ -f "$LOCK_DIR_PATH/charger_facing_disabled.lck" ]]; then
        DISABLE_CHARGER_FACING=true
    fi
fi

if [ "$REPAIR" = true ]; then
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
    if [ "$ENABLE_CELERY" = false ] && [ "$CELERY_MODE" != "disable" ] && [ -f "$LOCK_DIR_PATH/celery.lck" ]; then
        ENABLE_CELERY=true
    fi
    if [ "$SERVICE_MANAGEMENT_MODE_FLAG" = false ]; then
        SERVICE_MANAGEMENT_MODE="$(arthexis_detect_service_mode "$LOCK_DIR_PATH")"
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
    if [ "$ENABLE_IMAGER_BURNER_SERVICE" = false ] && arthexis_systemd_unit_recorded "$LOCK_DIR_PATH" "$ARTHEXIS_IMAGER_BURNER_SERVICE_UNIT"; then
        ENABLE_IMAGER_BURNER_SERVICE=true
        DISABLE_IMAGER_BURNER_SERVICE=false
    fi
    if [ "$ENABLE_BOOT_UPGRADE" = false ] && [ -f "$LOCK_DIR_PATH/boot-upgrade.lck" ]; then
        ENABLE_BOOT_UPGRADE=true
        DISABLE_BOOT_UPGRADE=false
    fi
    if [[ "$ENABLE_CHARGER_FACING" == false && "$ENABLE_OCPP_GATEWAY" == false && "$DISABLE_CHARGER_FACING" == false ]]; then
        if [[ -f "$LOCK_DIR_PATH/charger_facing.lck" ]]; then
            ENABLE_CHARGER_FACING=true
        fi
        if [[ -f "$LOCK_DIR_PATH/ocpp_gateway.lck" ]]; then
            ENABLE_OCPP_GATEWAY=true
        fi
    fi
    if [ "$ENABLE_CONTROL" = false ] && [ -f "$LOCK_DIR_PATH/control.lck" ]; then
        ENABLE_CONTROL=true
    fi
fi

apply_role_charger_facing_default

if [ -z "$PORT" ]; then
    PORT="$(arthexis_detect_backend_port "$SCRIPT_DIR")"
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
if [ "$ENABLE_CELERY" = false ] && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
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

# Resolve Python 3 interpreter used during install and bootstrap.
if ! PYTHON_BOOTSTRAP_BIN="$(arthexis_python_bin)"; then
    echo "Python 3 interpreter not found (tried python3, python, and python3.x aliases)." >&2
    exit 1
fi

if [ "$REPAIR" = true ] && [ -n "$SERVICE" ]; then
    reset_service_units_for_repair "$SERVICE"
    stop_existing_units_for_repair "$SERVICE"
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
    ensure_i2c_packages "$PYTHON_BOOTSTRAP_BIN"
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

BOOT_UPGRADE_LOCK="$LOCK_DIR/boot-upgrade.lck"
if [ "$ENABLE_BOOT_UPGRADE" = true ]; then
    touch "$BOOT_UPGRADE_LOCK"
elif [ "$DISABLE_BOOT_UPGRADE" = true ]; then
    rm -f "$BOOT_UPGRADE_LOCK"
fi

CONTROL_LOCK="$LOCK_DIR/control.lck"
if [ "$ENABLE_CONTROL" = true ]; then
    touch "$CONTROL_LOCK"
else
    rm -f "$CONTROL_LOCK"
fi

CHARGER_FACING_LOCK="$LOCK_DIR/charger_facing.lck"
OCPP_GATEWAY_LOCK="$LOCK_DIR/ocpp_gateway.lck"
CHARGER_FACING_DISABLED_LOCK="$LOCK_DIR/charger_facing_disabled.lck"
if [[ "$ENABLE_CHARGER_FACING" == true ]]; then
    touch "$CHARGER_FACING_LOCK"
elif [[ "$DISABLE_CHARGER_FACING" == true ]]; then
    rm -f "$CHARGER_FACING_LOCK"
fi
if [[ "$ENABLE_OCPP_GATEWAY" == true ]]; then
    touch "$OCPP_GATEWAY_LOCK"
elif [[ "$DISABLE_CHARGER_FACING" == true ]]; then
    rm -f "$OCPP_GATEWAY_LOCK"
fi
if [[ "$ENABLE_CHARGER_FACING" == true || "$ENABLE_OCPP_GATEWAY" == true ]]; then
    rm -f "$CHARGER_FACING_DISABLED_LOCK"
elif [[ "$DISABLE_CHARGER_FACING" == true ]]; then
    touch "$CHARGER_FACING_DISABLED_LOCK"
fi

collect_requirement_files() {
    local -n out_array="$1"
    local -a names=(requirements.txt)

    if [ "${ARTHEXIS_INCLUDE_QA_REQUIREMENTS:-0}" = "1" ]; then
        names+=(requirements-ci.txt)
    fi
    if [ "${ARTHEXIS_INCLUDE_HARDWARE_REQUIREMENTS:-0}" = "1" ]; then
        names+=(requirements-hw.txt)
    fi

    out_array=()
    local name
    for name in "${names[@]}"; do
        if [ -f "$BASE_DIR/$name" ]; then
            out_array+=("$BASE_DIR/$name")
        fi
    done
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

if [ "$ENABLE_CONTROL" = true ] || [ "$ENABLE_RFID_SERVICE" = true ] || [ "$ENABLE_LCD_SCREEN" = true ] \
    || [ -f "$LOCK_DIR/control.lck" ] \
    || { [ -f "$LOCK_DIR/role.lck" ] && [ "$(tr -d '\r\n' < "$LOCK_DIR/role.lck")" = "Control" ]; } \
    || [ -f "$LOCK_DIR/$ARTHEXIS_LCD_LOCK" ] || [ -f "$LOCK_DIR/$ARTHEXIS_RFID_SERVICE_LOCK" ] || [ -f "${LOCK_DIR}/${ARTHEXIS_RFID_LOCK:-rfid.lck}" ]; then
    ARTHEXIS_INCLUDE_HARDWARE_REQUIREMENTS=1
fi
export ARTHEXIS_INCLUDE_HARDWARE_REQUIREMENTS="${ARTHEXIS_INCLUDE_HARDWARE_REQUIREMENTS:-0}"
export ARTHEXIS_INCLUDE_QA_REQUIREMENTS="${ARTHEXIS_INCLUDE_QA_REQUIREMENTS:-0}"

VENV_RESOLVE_ARGS=("$BASE_DIR")
if [ "$ARTHEXIS_INCLUDE_QA_REQUIREMENTS" = "1" ]; then
    VENV_RESOLVE_ARGS+=(--include-ci)
fi
if [ "$ARTHEXIS_INCLUDE_HARDWARE_REQUIREMENTS" = "1" ]; then
    VENV_RESOLVE_ARGS+=(--include-hardware)
fi
VENV_DIR="$($PYTHON_BOOTSTRAP_BIN "$BASE_DIR/scripts/helpers/venv_path.py" "${VENV_RESOLVE_ARGS[@]}")"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
VENV_ACTIVATE="$VENV_DIR/bin/activate"

resolved_path() {
    "$PYTHON_BOOTSTRAP_BIN" - "$1" <<'PY'
from pathlib import Path
import sys

print(Path(sys.argv[1]).expanduser().resolve(strict=False))
PY
}

venv_dir_is_auto_managed() {
    local resolved_venv
    local checkout_venv
    local env_root_venvs

    resolved_venv="$(resolved_path "$VENV_DIR")"
    checkout_venv="$(resolved_path "$BASE_DIR/.venv")"
    if [[ "$resolved_venv" == "$checkout_venv" ]]; then
        return 0
    fi

    if [[ -n "${ARTHEXIS_ENV_ROOT:-}" ]]; then
        env_root_venvs="$("$PYTHON_BOOTSTRAP_BIN" - <<'PY'
from pathlib import Path
import os

print((Path(os.environ["ARTHEXIS_ENV_ROOT"]).expanduser().resolve(strict=False) / "venvs").resolve(strict=False))
PY
)"
        case "$resolved_venv" in
            "$env_root_venvs"/*)
                return 0
                ;;
        esac
    fi

    return 1
}

recreate_virtualenv() {
    local reason="$1"

    echo "$reason" >&2
    if ! venv_dir_is_auto_managed; then
        echo "Refusing to remove unmanaged virtual environment path: $VENV_DIR" >&2
        echo "Clean or correct ARTHEXIS_VENV_DIR manually, or unset it to use the checkout-local venv or ARTHEXIS_ENV_ROOT cache." >&2
        exit 1
    fi

    rm -rf "$VENV_DIR"
    mkdir -p "$(dirname "$VENV_DIR")"
    if ! "$PYTHON_BOOTSTRAP_BIN" -m venv "$VENV_DIR"; then
        echo "Failed to recreate virtual environment at $VENV_DIR. Ensure the python3-venv package is installed (e.g. sudo apt install python3-venv)." >&2
        exit 1
    fi
    NEW_VENV=true
}

arthexis_timing_start "virtualenv_setup"
# Create virtual environment if missing.
NEW_VENV=false
if [ ! -d "$VENV_DIR" ]; then
    mkdir -p "$(dirname "$VENV_DIR")"
    if ! "$PYTHON_BOOTSTRAP_BIN" -m venv "$VENV_DIR"; then
        echo "Failed to create virtual environment at $VENV_DIR. Ensure the python3-venv package is installed (e.g. sudo apt install python3-venv)." >&2
        exit 1
    fi
    NEW_VENV=true
    arthexis_timing_end "virtualenv_setup" "created"
else
    arthexis_timing_end "virtualenv_setup" "existing"
fi

if [ ! -f "$VENV_ACTIVATE" ]; then
    recreate_virtualenv "Virtual environment activation script not found at $VENV_ACTIVATE. Attempting to recreate the virtual environment."
fi

if [ ! -f "$VENV_ACTIVATE" ]; then
    echo "Virtual environment activation script not found at $VENV_ACTIVATE after recreation. The virtual environment may be corrupted. On Debian/Ubuntu, you may need to install the 'python3-venv' package." >&2
    exit 1
fi

venv_is_runnable() {
    # A cached virtualenv can become stale in CI when setup-python rolls forward
    # to a newer patch release, leaving entrypoints with a missing shebang
    # interpreter (e.g. bin/pip -> old toolcache path).
    "$VENV_PYTHON" -c 'import sys; print(sys.executable)' >/dev/null 2>&1 || return 1
    "$VENV_PIP" --version >/dev/null 2>&1 || return 1
    return 0
}

if ! venv_is_runnable; then
    recreate_virtualenv "Detected stale virtual environment binaries in $VENV_DIR. Recreating virtual environment."
fi

echo "$PORT" > "$LOCK_DIR/backend_port.lck"
PREVIOUS_NODE_ROLE=""
if [ -f "$LOCK_DIR/role.lck" ]; then
    PREVIOUS_NODE_ROLE="$(tr -d '[:space:]' < "$LOCK_DIR/role.lck")"
fi
echo "$NODE_ROLE" > "$LOCK_DIR/role.lck"

EXISTING_INSTALL=false
if [ -f "$BASE_DIR/migration.env" ]; then
    EXISTING_INSTALL=true
fi

if [[ -f "$LOCK_DIR/enabled_apps.lck" && -n "$PREVIOUS_NODE_ROLE" && "${PREVIOUS_NODE_ROLE,,}" != "${NODE_ROLE,,}" ]]; then
    write_role_enabled_apps_lock "$NODE_ROLE" refresh
elif role_app_profile_inputs_present; then
    write_role_enabled_apps_lock "$NODE_ROLE" refresh
elif [ "$EXISTING_INSTALL" = false ] && [ "$NODE_ROLE_EXPLICIT" = true ]; then
    write_role_enabled_apps_lock "$NODE_ROLE"
elif charger_facing_route_refresh_required && [[ -f "$LOCK_DIR/enabled_apps.lck" ]]; then
    refresh_charger_facing_route_lock_metadata
elif role_app_profiles_explicitly_enabled; then
    write_role_enabled_apps_lock "$NODE_ROLE"
else
    echo "Install without explicit role app profile opt-in; preserving full app fallback."
fi

if [ -z "${ARTHEXIS_MIGRATION_POLICY:-}" ]; then
    if [ "$EXISTING_INSTALL" = true ]; then
        ARTHEXIS_MIGRATION_POLICY="check"
    else
        case "${NODE_ROLE,,}" in
            satellite|watchtower)
                ARTHEXIS_MIGRATION_POLICY="check"
                ;;
            *)
                ARTHEXIS_MIGRATION_POLICY="apply"
                ;;
        esac
    fi
fi

case "${ARTHEXIS_MIGRATION_POLICY,,}" in
    apply|check|skip)
        ARTHEXIS_MIGRATION_POLICY="${ARTHEXIS_MIGRATION_POLICY,,}"
        ;;
    *)
        echo "Warning: Invalid ARTHEXIS_MIGRATION_POLICY value '${ARTHEXIS_MIGRATION_POLICY}', using role default." >&2
        if [ "$EXISTING_INSTALL" = true ]; then
            ARTHEXIS_MIGRATION_POLICY="check"
        else
            case "${NODE_ROLE,,}" in
                satellite|watchtower)
                    ARTHEXIS_MIGRATION_POLICY="check"
                    ;;
                *)
                    ARTHEXIS_MIGRATION_POLICY="apply"
                    ;;
            esac
        fi
        ;;
esac

printf 'ARTHEXIS_MIGRATION_POLICY=%s\n' "$ARTHEXIS_MIGRATION_POLICY" > "$BASE_DIR/migration.env"

if [ -z "${ARTHEXIS_COLLECTSTATIC_POLICY:-}" ]; then
    if [ "$EXISTING_INSTALL" = true ]; then
        ARTHEXIS_COLLECTSTATIC_POLICY="check"
    else
        ARTHEXIS_COLLECTSTATIC_POLICY="apply"
    fi
fi

case "${ARTHEXIS_COLLECTSTATIC_POLICY,,}" in
    apply|check|skip)
        ARTHEXIS_COLLECTSTATIC_POLICY="${ARTHEXIS_COLLECTSTATIC_POLICY,,}"
        ;;
    *)
        echo "Warning: Invalid ARTHEXIS_COLLECTSTATIC_POLICY value '${ARTHEXIS_COLLECTSTATIC_POLICY}', using install default." >&2
        if [ "$EXISTING_INSTALL" = true ]; then
            ARTHEXIS_COLLECTSTATIC_POLICY="check"
        else
            ARTHEXIS_COLLECTSTATIC_POLICY="apply"
        fi
        ;;
esac

printf 'ARTHEXIS_COLLECTSTATIC_POLICY=%s\n' "$ARTHEXIS_COLLECTSTATIC_POLICY" > "$BASE_DIR/collectstatic.env"

source "$VENV_ACTIVATE"
arthexis_timing_start "pip_bootstrap"
REQ_HASH_FILE="$VENV_DIR/.arthexis-requirements.bundle.sha256"
PIP_VERSION_MARKER="$VENV_DIR/.arthexis-pip.version"
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
env_refresh_args=(--force-refresh --install-and-refresh)
if [ "$CHANNEL" = "unstable" ] || [ "$CHANNEL" = "latest" ]; then
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
arthexis_timing_record "django_migrate" 0 "delegated-to-env-refresh"
if ls data/*.json >/dev/null 2>&1; then
    arthexis_timing_start "load_user_data"
    python manage.py loaddata data/*.json
    arthexis_timing_end "load_user_data"
else
    arthexis_timing_record "load_user_data" 0 "skipped"
fi
arthexis_timing_record "env_refresh" 0 "included-in-requirements_install"

# Ensure auto-managed node features are refreshed via NodeFeatureAssignment lifecycle.
python manage.py features --refresh-node

deactivate


# If a service name was provided, install a systemd unit and persist its name
if [ -n "$SERVICE" ]; then
    echo "$SERVICE" > "$LOCK_DIR/service.lck"
    if [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
        arthexis_record_systemd_unit "$LOCK_DIR" "${SERVICE}.service"
    fi
    EXEC_CMD="$BASE_DIR/scripts/service-start.sh"
    arthexis_install_service_stack "$BASE_DIR" "$LOCK_DIR" "$SERVICE" "$ENABLE_CELERY" "$EXEC_CMD" "$SERVICE_MANAGEMENT_MODE" "$ENABLE_BOOT_UPGRADE"
fi

if [ -n "$SERVICE" ]; then
    LCD_SERVICE="lcd-$SERVICE"
    RFID_SERVICE="rfid-$SERVICE"
    CAMERA_SERVICE="camera-$SERVICE"
    if [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
        if [ "$ENABLE_CONTROL" = true ]; then
            arthexis_install_control_usb_polling_timer_overrides "$LOCK_DIR"
        else
            arthexis_remove_control_usb_polling_timer_overrides
        fi

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
        elif [ "$ENABLE_LCD_SCREEN" = true ]; then
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

        if [ "$DISABLE_IMAGER_BURNER_SERVICE" = true ]; then
            arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "$ARTHEXIS_IMAGER_BURNER_SERVICE_UNIT"
        elif [ "$ENABLE_IMAGER_BURNER_SERVICE" = true ]; then
            arthexis_install_imager_burner_service_unit "$BASE_DIR" "$LOCK_DIR" "$SERVICE"
        else
            arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "$ARTHEXIS_IMAGER_BURNER_SERVICE_UNIT"
        fi

        if [ "$ENABLE_BOOT_UPGRADE" = true ]; then
            arthexis_install_boot_upgrade_service_unit "$BASE_DIR" "$LOCK_DIR" "$SERVICE"
        else
            arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${SERVICE}-boot-upgrade.service"
            rm -f "$LOCK_DIR/${SERVICE}-boot-upgrade-backoff-until.lck"
        fi
    else
        arthexis_remove_control_usb_polling_timer_overrides
        if arthexis_systemd_unit_recorded "$LOCK_DIR" "${LCD_SERVICE}.service"; then
            arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${LCD_SERVICE}.service"
        fi
        if arthexis_systemd_unit_recorded "$LOCK_DIR" "${RFID_SERVICE}.service"; then
            arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${RFID_SERVICE}.service"
        fi
        if arthexis_systemd_unit_recorded "$LOCK_DIR" "${CAMERA_SERVICE}.service"; then
            arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${CAMERA_SERVICE}.service"
        fi
        if arthexis_systemd_unit_recorded "$LOCK_DIR" "$ARTHEXIS_IMAGER_BURNER_SERVICE_UNIT"; then
            arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "$ARTHEXIS_IMAGER_BURNER_SERVICE_UNIT"
        fi
        if arthexis_systemd_unit_recorded "$LOCK_DIR" "${SERVICE}-boot-upgrade.service"; then
            arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${SERVICE}-boot-upgrade.service"
        fi
        rm -f "$LOCK_DIR/${SERVICE}-boot-upgrade-backoff-until.lck"
    fi
fi


if [ "$AUTO_UPGRADE" = true ]; then
    rm -f AUTO_UPGRADE
    echo "$CHANNEL" > "$LOCK_DIR/auto_upgrade.lck"
    if [ "$UPGRADE" = true ]; then
        if [ "$CHANNEL" = "unstable" ] || [ "$CHANNEL" = "latest" ]; then
            ./upgrade.sh --latest
        elif [ "$CHANNEL" = "regular" ] || [ "$CHANNEL" = "normal" ]; then
            ./upgrade.sh --regular
        else
            ./upgrade.sh --stable
        fi
    fi
    source "$VENV_ACTIVATE"
    python manage.py shell <<'PYCODE'
from apps.core.auto_upgrade import ensure_auto_upgrade_periodic_task

ensure_auto_upgrade_periodic_task()
PYCODE
    deactivate
elif [ "$UPGRADE" = true ]; then
    if [ "$CHANNEL" = "unstable" ] || [ "$CHANNEL" = "latest" ]; then
        ./upgrade.sh --latest
    elif [ "$CHANNEL" = "regular" ] || [ "$CHANNEL" = "normal" ]; then
        ./upgrade.sh --regular
    else
        ./upgrade.sh --stable
    fi
elif [ "$AUTO_UPGRADE" = false ]; then
    rm -f "$LOCK_DIR/auto_upgrade.lck"
elif [ -n "$SERVICE" ] && [ "$START_FLAG" = false ] && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
    sudo systemctl restart "$SERVICE"
    if [ "$ENABLE_CELERY" = true ]; then
        sudo systemctl restart "celery-$SERVICE"
        sudo systemctl restart "celery-beat-$SERVICE"
    fi
fi

if [ "$START_SERVICES" = true ]; then
    "$BASE_DIR/start.sh"
fi
