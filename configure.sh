#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$SCRIPT_DIR"
SCRIPTS_DIR="$BASE_DIR/scripts"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/git_remote.sh
. "$BASE_DIR/scripts/helpers/git_remote.sh"
# shellcheck source=scripts/helpers/ports.sh
. "$BASE_DIR/scripts/helpers/ports.sh"
# shellcheck source=scripts/helpers/service_manager.sh
. "$BASE_DIR/scripts/helpers/service_manager.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

arthexis_ensure_upstream_remotes "$BASE_DIR"

SERVICE=""
NODE_ROLE=""
PORT=""
ENABLE_CELERY=false
ENABLE_LCD_SCREEN=false
ENABLE_CONTROL=false
ENABLE_RFID_SERVICE=false
ENABLE_CAMERA_SERVICE=false
REQUIRES_REDIS=false
UPGRADE_CHANNEL=""
CHECK=false
AUTO_UPGRADE_MODE=""
DEBUG_MODE=""
DEBUG_OVERRIDDEN=false
REPAIR=false
SKIP_SERVICE_RESTART=false
REPAIR_AUTO_UPGRADE_CHANNEL=""
FAILOVER_ROLE=""
RFID_SERVICE_MODE=""
CAMERA_SERVICE_MODE=""
BOOT_UPGRADE_MODE=""
CELERY_MODE=""
LCD_SCREEN_MODE=""
FEATURE_SLUG=""
FEATURE_KIND=""
FEATURE_MODE=""
FEATURE_PARAM_SPEC=""

LOCK_DIR="$BASE_DIR/.locks"

usage() {
    echo "Usage: $0 [--service NAME] [--port PORT] [--latest|--stable|--regular|--normal|--unstable] [--fixed] [--check] [--auto-upgrade|--no-auto-upgrade] [--debug|--no-debug] [--celery|--no-celery] [--lcd-screen|--no-lcd-screen] [--rfid-service|--no-rfid-service] [--camera-service|--no-camera-service] [--boot-upgrade|--no-boot-upgrade] [--feature SLUG [--kind suite|node] [--enabled|--disabled]] [--feature-param FEATURE:KEY=VALUE] [--satellite|--terminal|--control|--watchtower] [--repair [--failover ROLE]]" >&2
    exit 1
}

write_debug_env() {
    cat > "$BASE_DIR/debug.env" <<EOF
DEBUG=$1
EOF
}

apply_rfid_service_setting() {
    local mode="$1"
    local lock_dir="$2"
    local base_dir="$3"
    local service_name="$4"

    if [ -z "$mode" ]; then
        return 0
    fi

    mkdir -p "$lock_dir"
    if [ "$mode" = "enable" ]; then
        touch "$lock_dir/$ARTHEXIS_RFID_SERVICE_LOCK"
        if [ -n "$service_name" ] && arthexis_using_systemd_mode "$lock_dir"; then
            arthexis_install_rfid_service_unit "$base_dir" "$lock_dir" "$service_name"
            arthexis_start_systemd_unit_if_present "rfid-${service_name}.service"
        fi
        echo "RFID scanner service enabled."
    else
        rm -f "$lock_dir/$ARTHEXIS_RFID_SERVICE_LOCK"
        if [ -n "$service_name" ]; then
            arthexis_remove_systemd_unit_if_present "$lock_dir" "rfid-${service_name}.service"
        fi
        echo "RFID scanner service disabled."
    fi
}

apply_camera_service_setting() {
    local mode="$1"
    local lock_dir="$2"
    local base_dir="$3"
    local service_name="$4"

    if [ -z "$mode" ]; then
        return 0
    fi

    mkdir -p "$lock_dir"
    if [ "$mode" = "enable" ]; then
        touch "$lock_dir/$ARTHEXIS_CAMERA_SERVICE_LOCK"
        if [ -n "$service_name" ] && arthexis_using_systemd_mode "$lock_dir"; then
            arthexis_install_camera_service_unit "$base_dir" "$lock_dir" "$service_name"
            arthexis_start_systemd_unit_if_present "camera-${service_name}.service"
        fi
        echo "Camera service enabled."
    else
        rm -f "$lock_dir/$ARTHEXIS_CAMERA_SERVICE_LOCK"
        if [ -n "$service_name" ]; then
            arthexis_remove_systemd_unit_if_present "$lock_dir" "camera-${service_name}.service"
        fi
        echo "Camera service disabled."
    fi
}

run_feature_toggle() {
    local slug="$1"
    local kind="$2"
    local mode="$3"
    local python_bin=""
    local -a args=("$BASE_DIR/manage.py" "feature" "$slug")

    if [ -x "$BASE_DIR/.venv/bin/python" ]; then
        python_bin="$BASE_DIR/.venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        python_bin="$(command -v python3)"
    else
        echo "Python 3 is required to toggle features." >&2
        exit 1
    fi

    if [ -n "$kind" ]; then
        args+=("--kind" "$kind")
    fi
    if [ "$mode" = "enable" ]; then
        args+=("--enabled")
    elif [ "$mode" = "disable" ]; then
        args+=("--disabled")
    fi

    "$python_bin" "${args[@]}"
}

run_feature_param_set() {
    local feature="$1"
    local key="$2"
    local value="$3"
    local python_bin=""

    if [ -x "$BASE_DIR/.venv/bin/python" ]; then
        python_bin="$BASE_DIR/.venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        python_bin="$(command -v python3)"
    else
        echo "Python 3 is required to set suite feature parameters." >&2
        exit 1
    fi

    FEATURE_PARAM_SLUG="$feature" FEATURE_PARAM_KEY="$key" FEATURE_PARAM_VALUE="$value" \
        "$python_bin" "$BASE_DIR/manage.py" shell <<'PYCODE'
import os
from django.core.management.base import CommandError
from apps.features.models import Feature

slug = os.environ["FEATURE_PARAM_SLUG"]
key = os.environ["FEATURE_PARAM_KEY"]
value = os.environ["FEATURE_PARAM_VALUE"]

feature = Feature.objects.filter(slug=slug).first()
if feature is None:
    raise CommandError(f"Unknown suite feature: {slug}")

metadata = dict(feature.metadata or {})
parameters = dict(metadata.get("parameters") or {})
parameters[key] = value
metadata["parameters"] = parameters
feature.metadata = metadata
feature.save(update_fields=["metadata", "updated_at"])
print(f"- {slug} parameter {key} set to '{value}'")
PYCODE
}

reset_role_features() {
    ENABLE_CELERY=false
    ENABLE_LCD_SCREEN=false
    ENABLE_CONTROL=false
    REQUIRES_REDIS=false
}

configure_role_from_name() {
    local resolved_role="$1"

    reset_role_features

    case "$resolved_role" in
        Satellite)
            NODE_ROLE="Satellite"
            ENABLE_CELERY=true
            REQUIRES_REDIS=true
            ;;
        Terminal)
            NODE_ROLE="Terminal"
            ENABLE_CELERY=true
            ;;
        Control)
            NODE_ROLE="Control"
            ENABLE_CELERY=true
            ENABLE_LCD_SCREEN=true
            ENABLE_CONTROL=true
            REQUIRES_REDIS=true
            ;;
        Watchtower)
            NODE_ROLE="Watchtower"
            ENABLE_CELERY=true
            REQUIRES_REDIS=true
            ;;
        *)
            return 1
            ;;
    esac

    return 0
}

resolve_role_from_value() {
    local raw_role="$1"
    local normalized_role="${raw_role//[$'\r\n\t ']}"
    local lower_role="${normalized_role,,}"

    case "$lower_role" in
        satellite)
            echo "Satellite"
            return 0
            ;;
        terminal)
            echo "Terminal"
            return 0
            ;;
        control)
            echo "Control"
            return 0
            ;;
        watchtower)
            echo "Watchtower"
            return 0
            ;;
        constellation)
            echo "Watchtower"
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

detect_role_from_environment() {
    if [ -f "$LOCK_DIR/control.lck" ] || arthexis_lcd_feature_enabled "$LOCK_DIR"; then
        echo "Control"
        return 0
    fi

    for role_file in "$BASE_DIR"/*.role "$BASE_DIR"/.*.role; do
        [ -f "$role_file" ] || continue
        local role_name
        role_name=$(basename "$role_file")
        role_name=${role_name#.}
        role_name=${role_name%.role}
        local resolved
        resolved=$(resolve_role_from_value "$role_name" 2>/dev/null || true)
        if [ -n "$resolved" ]; then
            echo "$resolved"
            return 0
        fi
    done

    return 1
}

set_role_from_lock() {
    local stored_role="$1"
    local resolved_role=""
    local resolution_method="stored"

    if [ -n "$stored_role" ]; then
        resolved_role=$(resolve_role_from_value "$stored_role" 2>/dev/null || true)
    fi

    if [ -z "$resolved_role" ]; then
        resolution_method="detected"
        resolved_role=$(detect_role_from_environment 2>/dev/null || true)
    fi

    if [ -z "$resolved_role" ]; then
        return 1
    fi

    if ! configure_role_from_name "$resolved_role"; then
        return 1
    fi

    local stored_lower="${stored_role,,}"
    local resolved_lower="${resolved_role,,}"

    if [ "$resolution_method" = "stored" ] && [ -n "$stored_role" ] && [ "$stored_lower" != "$resolved_lower" ]; then
        echo "Stored node role '$stored_role' is deprecated; using '$resolved_role' instead."
    elif [ "$resolution_method" = "detected" ]; then
        if [ -n "$stored_role" ]; then
            echo "Stored node role '$stored_role' is not recognized; detected '$resolved_role' from existing configuration."
        else
            echo "Stored node role was empty; detected '$resolved_role' from existing configuration."
        fi
    fi

    return 0
}

apply_failover_role() {
    local failover_value="$1"
    local resolved_role
    resolved_role=$(resolve_role_from_value "$failover_value" 2>/dev/null || true)
    if [ -z "$resolved_role" ]; then
        return 1
    fi

    if ! configure_role_from_name "$resolved_role"; then
        return 1
    fi

    local provided_lower="${failover_value,,}"
    local resolved_lower="${resolved_role,,}"
    if [ "$provided_lower" != "$resolved_lower" ]; then
        echo "Failover role '$failover_value' is deprecated; using '$resolved_role' instead."
    else
        echo "Using failover role '$resolved_role' for repair."
    fi

    return 0
}



require_redis() {
    if ! command -v redis-cli >/dev/null 2>&1; then
        echo "Redis is required for the $1 role but is not installed." >&2
        echo "Install redis-server and re-run this script. For Debian/Ubuntu:" >&2
        echo "  sudo apt update && sudo apt install redis-server" >&2
        exit 1
    fi
    if ! redis-cli ping >/dev/null 2>&1; then
        echo "Redis is required for the $1 role but does not appear to be running." >&2
        echo "Start redis and re-run this script. For Debian/Ubuntu:" >&2
        echo "  sudo systemctl start redis-server" >&2
        exit 1
    fi
    cat > "$SCRIPTS_DIR/redis.env" <<'EOF_REDIS'
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
EOF_REDIS
}

run_auto_upgrade_management() {
    local action="$1"
    local python_bin=""

    if [ -x "$BASE_DIR/.venv/bin/python" ]; then
        python_bin="$BASE_DIR/.venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        python_bin="$(command -v python3)"
    else
        return
    fi

    if [ "$action" = "enable" ]; then
        "$python_bin" "$BASE_DIR/manage.py" shell <<'PYCODE' || true
from apps.core.auto_upgrade import ensure_auto_upgrade_periodic_task

ensure_auto_upgrade_periodic_task()
PYCODE
    else
        "$python_bin" "$BASE_DIR/manage.py" shell <<'PYCODE' || true
from apps.core.auto_upgrade import AUTO_UPGRADE_TASK_NAME
try:
    from django_celery_beat.models import PeriodicTask
except Exception:
    pass
else:
    PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).delete()
PYCODE
    fi
}

set_upgrade_channel() {
    local new_channel="$1"

    if [ -n "$UPGRADE_CHANNEL" ] && [ "$UPGRADE_CHANNEL" != "$new_channel" ]; then
        echo "Only one of --latest, --stable, or --regular may be specified" >&2
        usage
    fi

    UPGRADE_CHANNEL="$new_channel"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --service)
            [ -z "$2" ] && usage
            SERVICE="$2"
            shift 2
            ;;
        --latest)
            set_upgrade_channel "latest"
            shift
            ;;
        --stable)
            set_upgrade_channel "stable"
            shift
            ;;
        --regular|--normal)
            set_upgrade_channel "version"
            shift
            ;;
        --unstable)
            set_upgrade_channel "latest"
            shift
            ;;
        --fixed)
            if [ "$AUTO_UPGRADE_MODE" = "enable" ]; then
                echo "Cannot combine --fixed with --auto-upgrade or channel flags" >&2
                usage
            fi
            AUTO_UPGRADE_MODE="disable"
            shift
            ;;
        --port)
            [ -z "$2" ] && usage
            PORT="$2"
            shift 2
            ;;
        --check)
            CHECK=true
            shift
            ;;
        --auto-upgrade)
            if [ "$AUTO_UPGRADE_MODE" = "disable" ]; then
                echo "Cannot combine --auto-upgrade with --no-auto-upgrade" >&2
                usage
            fi
            AUTO_UPGRADE_MODE="enable"
            shift
            ;;
        --no-auto-upgrade)
            if [ "$AUTO_UPGRADE_MODE" = "enable" ]; then
                echo "Cannot combine --auto-upgrade with --no-auto-upgrade" >&2
                usage
            fi
            AUTO_UPGRADE_MODE="disable"
            shift
            ;;
        --debug)
            if [ "$DEBUG_MODE" = "disable" ]; then
                echo "Cannot combine --debug with --no-debug" >&2
                usage
            fi
            DEBUG_MODE="enable"
            shift
            ;;
        --no-debug)
            if [ "$DEBUG_MODE" = "enable" ]; then
                echo "Cannot combine --debug with --no-debug" >&2
                usage
            fi
            DEBUG_MODE="disable"
            shift
            ;;
        --rfid-service)
            if [ "$RFID_SERVICE_MODE" = "disable" ]; then
                echo "Cannot combine --rfid-service with --no-rfid-service" >&2
                usage
            fi
            RFID_SERVICE_MODE="enable"
            shift
            ;;
        --no-rfid-service)
            if [ "$RFID_SERVICE_MODE" = "enable" ]; then
                echo "Cannot combine --rfid-service with --no-rfid-service" >&2
                usage
            fi
            RFID_SERVICE_MODE="disable"
            shift
            ;;
        --camera-service)
            if [ "$CAMERA_SERVICE_MODE" = "disable" ]; then
                echo "Cannot combine --camera-service with --no-camera-service" >&2
                usage
            fi
            CAMERA_SERVICE_MODE="enable"
            shift
            ;;
        --no-camera-service)
            if [ "$CAMERA_SERVICE_MODE" = "enable" ]; then
                echo "Cannot combine --camera-service with --no-camera-service" >&2
                usage
            fi
            CAMERA_SERVICE_MODE="disable"
            shift
            ;;
        --boot-upgrade)
            if [ "$BOOT_UPGRADE_MODE" = "disable" ]; then
                echo "Cannot combine --boot-upgrade with --no-boot-upgrade" >&2
                usage
            fi
            BOOT_UPGRADE_MODE="enable"
            shift
            ;;
        --no-boot-upgrade)
            if [ "$BOOT_UPGRADE_MODE" = "enable" ]; then
                echo "Cannot combine --boot-upgrade with --no-boot-upgrade" >&2
                usage
            fi
            BOOT_UPGRADE_MODE="disable"
            shift
            ;;
        --celery)
            if [ "$CELERY_MODE" = "disable" ]; then
                echo "Cannot combine --celery with --no-celery" >&2
                usage
            fi
            CELERY_MODE="enable"
            shift
            ;;
        --no-celery)
            if [ "$CELERY_MODE" = "enable" ]; then
                echo "Cannot combine --celery with --no-celery" >&2
                usage
            fi
            CELERY_MODE="disable"
            shift
            ;;
        --lcd-screen)
            if [ "$LCD_SCREEN_MODE" = "disable" ]; then
                echo "Cannot combine --lcd-screen with --no-lcd-screen" >&2
                usage
            fi
            LCD_SCREEN_MODE="enable"
            shift
            ;;
        --no-lcd-screen)
            if [ "$LCD_SCREEN_MODE" = "enable" ]; then
                echo "Cannot combine --lcd-screen with --no-lcd-screen" >&2
                usage
            fi
            LCD_SCREEN_MODE="disable"
            shift
            ;;
        --feature)
            [ -z "$2" ] && usage
            FEATURE_SLUG="$2"
            shift 2
            ;;
        --kind)
            [ -z "$2" ] && usage
            case "$2" in
                suite|node)
                    FEATURE_KIND="$2"
                    ;;
                *)
                    echo "--kind must be one of: suite, node" >&2
                    usage
                    ;;
            esac
            shift 2
            ;;
        --enabled)
            if [ "$FEATURE_MODE" = "disable" ]; then
                echo "Cannot combine --enabled with --disabled" >&2
                usage
            fi
            FEATURE_MODE="enable"
            shift
            ;;
        --disabled)
            if [ "$FEATURE_MODE" = "enable" ]; then
                echo "Cannot combine --enabled with --disabled" >&2
                usage
            fi
            FEATURE_MODE="disable"
            shift
            ;;
        --feature-param)
            [ -z "$2" ] && usage
            FEATURE_PARAM_SPEC="$2"
            shift 2
            ;;
        --satellite)
            NODE_ROLE="Satellite"
            ENABLE_CELERY=true
            REQUIRES_REDIS=true
            shift
            ;;
        --terminal)
            NODE_ROLE="Terminal"
            ENABLE_CELERY=true
            shift
            ;;
        --control)
            NODE_ROLE="Control"
            ENABLE_CELERY=true
            ENABLE_LCD_SCREEN=true
            ENABLE_CONTROL=true
            REQUIRES_REDIS=true
            shift
            ;;
        --watchtower)
            NODE_ROLE="Watchtower"
            ENABLE_CELERY=true
            REQUIRES_REDIS=true
            shift
            ;;
        --repair)
            REPAIR=true
            shift
            ;;
        --failover)
            [ -z "$2" ] && usage
            FAILOVER_ROLE="$2"
            shift 2
            ;;
        *)
            usage
            ;;
    esac

done

if [ -n "$UPGRADE_CHANNEL" ] && [ -z "$AUTO_UPGRADE_MODE" ]; then
    AUTO_UPGRADE_MODE="enable"
fi

if [ -n "$FEATURE_MODE" ] && [ -z "$FEATURE_SLUG" ]; then
    echo "--enabled/--disabled requires --feature" >&2
    usage
fi

if [ -n "$FEATURE_KIND" ] && [ -z "$FEATURE_SLUG" ]; then
    echo "--kind requires --feature" >&2
    usage
fi

if [ -z "$PORT" ]; then
    PORT="$(arthexis_detect_backend_port "$BASE_DIR")"
fi

if [ -n "$FAILOVER_ROLE" ] && [ "$REPAIR" != true ]; then
    echo "--failover can only be used together with --repair" >&2
    usage
fi

if [ "$REPAIR" = true ]; then
    if [ "$CHECK" = true ] || [ -n "$NODE_ROLE" ] || [ -n "$SERVICE" ] || \
       [ -n "$AUTO_UPGRADE_MODE" ] || [ -n "$DEBUG_MODE" ] || [ -n "$UPGRADE_CHANNEL" ] || \
       [ -n "$RFID_SERVICE_MODE" ] || [ -n "$CAMERA_SERVICE_MODE" ] || [ -n "$BOOT_UPGRADE_MODE" ] || [ -n "$CELERY_MODE" ] || \
       [ -n "$LCD_SCREEN_MODE" ] || [ -n "$FEATURE_SLUG" ] || [ -n "$FEATURE_MODE" ] || \
       [ -n "$FEATURE_KIND" ] || [ -n "$FEATURE_PARAM_SPEC" ]; then
        echo "--repair cannot be combined with other options" >&2
        usage
    fi

    if [ ! -f "$LOCK_DIR/role.lck" ]; then
        echo "Cannot repair because the current node role is unknown." >&2
        exit 1
    fi

    stored_role=$(tr -d '\r\n\t ' < "$LOCK_DIR/role.lck" 2>/dev/null || true)
    if ! set_role_from_lock "$stored_role"; then
        if [ -n "$FAILOVER_ROLE" ]; then
            if ! apply_failover_role "$FAILOVER_ROLE"; then
                echo "Invalid failover role '$FAILOVER_ROLE'. Supported roles: Satellite, Terminal, Control, Watchtower." >&2
                exit 1
            fi
            if [ -n "$stored_role" ]; then
                echo "Unable to determine node role from stored value '$stored_role'; using failover role '$NODE_ROLE'."
            else
                echo "Unable to determine node role from lock files; using failover role '$NODE_ROLE'."
            fi
        else
            if [ -n "$stored_role" ]; then
                echo "Unable to determine node role from stored value '$stored_role'. Re-run with --failover <role> to specify the role to restore." >&2
            else
                echo "Unable to determine node role for repair. Re-run with --failover <role> to specify the role to restore." >&2
            fi
            exit 1
        fi
    elif [ -n "$FAILOVER_ROLE" ]; then
        echo "Failover role '$FAILOVER_ROLE' was not needed; continuing with the $NODE_ROLE role."
    fi
    SKIP_SERVICE_RESTART=true

    if [ -f "$LOCK_DIR/service.lck" ]; then
        SERVICE=$(cat "$LOCK_DIR/service.lck")
    fi

    if [ -f "$LOCK_DIR/auto_upgrade.lck" ]; then
        stored_channel=$(tr -d '\r\n\t ' < "$LOCK_DIR/auto_upgrade.lck" | tr '[:upper:]' '[:lower:]')
        case "$stored_channel" in
            latest|stable|version)
                REPAIR_AUTO_UPGRADE_CHANNEL="$stored_channel"
                ;;
            "")
                REPAIR_AUTO_UPGRADE_CHANNEL="version"
                ;;
            *)
                REPAIR_AUTO_UPGRADE_CHANNEL="version"
                ;;
        esac
        AUTO_UPGRADE_MODE="enable"
        UPGRADE_CHANNEL="$REPAIR_AUTO_UPGRADE_CHANNEL"
    fi
fi

if [ "$CHECK" = true ]; then
    if [ -n "$NODE_ROLE" ] || [ -n "$SERVICE" ] || [ -n "$AUTO_UPGRADE_MODE" ] || \
       [ -n "$DEBUG_MODE" ] || [ -n "$UPGRADE_CHANNEL" ] || [ -n "$RFID_SERVICE_MODE" ] || \
       [ -n "$CAMERA_SERVICE_MODE" ] || [ -n "$BOOT_UPGRADE_MODE" ] || [ -n "$CELERY_MODE" ] || [ -n "$LCD_SCREEN_MODE" ] || \
       [ -n "$FEATURE_SLUG" ] || [ -n "$FEATURE_MODE" ] || [ -n "$FEATURE_KIND" ] || \
       [ -n "$FEATURE_PARAM_SPEC" ]; then
        echo "--check cannot be combined with other options" >&2
        usage
    fi

    if [ -f "$LOCK_DIR/role.lck" ]; then
        echo "Role: $(cat "$LOCK_DIR/role.lck")"
    else
        echo "Role: unknown"
    fi

    echo "Configured port: $PORT"

    if [ -f "$LOCK_DIR/auto_upgrade.lck" ]; then
        case "$(cat "$LOCK_DIR/auto_upgrade.lck")" in
            latest)
                echo "Auto-upgrade: enabled (latest channel)"
                ;;
            stable)
                echo "Auto-upgrade: enabled (stable channel)"
                ;;
            version)
                echo "Auto-upgrade: enabled (regular channel)"
                ;;
            *)
                echo "Auto-upgrade: enabled (unknown channel)"
                ;;
        esac
    else
        echo "Auto-upgrade: disabled"
    fi

    if [ -f "$LOCK_DIR/boot-upgrade.lck" ]; then
        echo "Boot-upgrade pre-start: enabled"
    else
        echo "Boot-upgrade pre-start: disabled"
    fi

    if [ -f "$BASE_DIR/debug.env" ]; then
        debug_value=$(awk -F= '/^DEBUG=/{value=$2} END{print value}' "$BASE_DIR/debug.env")
        if [ "$debug_value" = "1" ]; then
            echo "Debug: enabled"
        else
            echo "Debug: disabled"
        fi
    else
        echo "Debug: disabled"
    fi

    exit 0
fi

ACTION_PERFORMED=false

if [ -n "$DEBUG_MODE" ]; then
    ACTION_PERFORMED=true
    DEBUG_OVERRIDDEN=true
    DEBUG_VALUE="0"
    DEBUG_MESSAGE="Debug mode disabled by default."
    if [ "$DEBUG_MODE" = "enable" ]; then
        DEBUG_VALUE="1"
        DEBUG_MESSAGE="Debug mode enabled by default."
    fi
    write_debug_env "$DEBUG_VALUE"
    echo "$DEBUG_MESSAGE"
fi

if [ -n "$RFID_SERVICE_MODE" ] && [ -z "$NODE_ROLE" ]; then
    ACTION_PERFORMED=true
    if [ -z "$SERVICE" ] && [ -f "$LOCK_DIR/service.lck" ]; then
        SERVICE=$(cat "$LOCK_DIR/service.lck")
    fi
    apply_rfid_service_setting "$RFID_SERVICE_MODE" "$LOCK_DIR" "$BASE_DIR" "$SERVICE"
fi

if [ -n "$CAMERA_SERVICE_MODE" ] && [ -z "$NODE_ROLE" ]; then
    ACTION_PERFORMED=true
    if [ -z "$SERVICE" ] && [ -f "$LOCK_DIR/service.lck" ]; then
        SERVICE=$(cat "$LOCK_DIR/service.lck")
    fi
    apply_camera_service_setting "$CAMERA_SERVICE_MODE" "$LOCK_DIR" "$BASE_DIR" "$SERVICE"
fi

if [ -n "$BOOT_UPGRADE_MODE" ] && [ -z "$NODE_ROLE" ]; then
    ACTION_PERFORMED=true
    local_enable_celery=false
    mkdir -p "$LOCK_DIR"
    if [ -z "$SERVICE" ] && [ -f "$LOCK_DIR/service.lck" ]; then
        SERVICE=$(cat "$LOCK_DIR/service.lck")
    fi
    if [ -f "$LOCK_DIR/celery.lck" ]; then
        local_enable_celery=true
    fi
    if [ -z "$SERVICE" ] || ! arthexis_using_systemd_mode "$LOCK_DIR"; then
        echo "Boot upgrade pre-start hook requires a configured systemd service." >&2
        exit 1
    fi
    if [ "$BOOT_UPGRADE_MODE" = "enable" ]; then
        touch "$LOCK_DIR/boot-upgrade.lck"
        arthexis_install_boot_upgrade_service_unit "$BASE_DIR" "$LOCK_DIR" "$SERVICE"
        arthexis_install_service_stack "$BASE_DIR" "$LOCK_DIR" "$SERVICE" "$local_enable_celery" "$BASE_DIR/scripts/service-start.sh" "$ARTHEXIS_SERVICE_MODE_SYSTEMD" true
        echo "Boot upgrade pre-start hook enabled."
    else
        rm -f "$LOCK_DIR/boot-upgrade.lck"
        rm -f "$LOCK_DIR/${SERVICE}-boot-upgrade-backoff-until.lck"
        arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${SERVICE}-boot-upgrade.service"
        arthexis_install_service_stack "$BASE_DIR" "$LOCK_DIR" "$SERVICE" "$local_enable_celery" "$BASE_DIR/scripts/service-start.sh" "$ARTHEXIS_SERVICE_MODE_SYSTEMD" false
        echo "Boot upgrade pre-start hook disabled."
    fi
fi

if [ -n "$CELERY_MODE" ] && [ -z "$NODE_ROLE" ]; then
    ACTION_PERFORMED=true
    mkdir -p "$LOCK_DIR"
    if [ "$CELERY_MODE" = "enable" ]; then
        touch "$LOCK_DIR/celery.lck"
        echo "Celery support enabled."
    else
        rm -f "$LOCK_DIR/celery.lck"
        if [ -n "$SERVICE" ] && arthexis_using_systemd_mode "$LOCK_DIR"; then
            arthexis_remove_celery_unit_stack "$LOCK_DIR" "$SERVICE"
        fi
        echo "Celery support disabled."
    fi
fi

if [ -n "$LCD_SCREEN_MODE" ] && [ -z "$NODE_ROLE" ]; then
    ACTION_PERFORMED=true
    mkdir -p "$LOCK_DIR"
    if [ "$LCD_SCREEN_MODE" = "enable" ]; then
        arthexis_enable_lcd_feature_flag "$LOCK_DIR"
        echo "LCD screen support enabled."
    else
        arthexis_disable_lcd_feature_flag "$LOCK_DIR"
        if [ -n "$SERVICE" ] && arthexis_using_systemd_mode "$LOCK_DIR"; then
            arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "lcd-${SERVICE}.service"
        fi
        echo "LCD screen support disabled."
    fi
fi

if [ -n "$FEATURE_SLUG" ] && [ -z "$NODE_ROLE" ]; then
    ACTION_PERFORMED=true
    run_feature_toggle "$FEATURE_SLUG" "$FEATURE_KIND" "$FEATURE_MODE"
fi

if [ -n "$FEATURE_PARAM_SPEC" ] && [ -z "$NODE_ROLE" ]; then
    ACTION_PERFORMED=true
    if [[ "$FEATURE_PARAM_SPEC" != *:*"="* ]]; then
        echo "--feature-param expects FEATURE:KEY=VALUE" >&2
        usage
    fi
    feature_part="${FEATURE_PARAM_SPEC%%:*}"
    key_value_part="${FEATURE_PARAM_SPEC#*:}"
    feature_key="${key_value_part%%=*}"
    feature_value="${key_value_part#*=}"
    if [ -z "$feature_part" ] || [ -z "$feature_key" ]; then
        echo "--feature-param expects FEATURE:KEY=VALUE" >&2
        usage
    fi
    run_feature_param_set "$feature_part" "$feature_key" "$feature_value"
fi

if [ -n "$AUTO_UPGRADE_MODE" ] && [ -z "$NODE_ROLE" ]; then
    ACTION_PERFORMED=true
    mkdir -p "$LOCK_DIR"
    if [ "$AUTO_UPGRADE_MODE" = "enable" ]; then
        channel="${UPGRADE_CHANNEL:-version}"
        echo "$channel" > "$LOCK_DIR/auto_upgrade.lck"
        run_auto_upgrade_management enable
        case "$channel" in
            latest)
                echo "Auto-upgrade enabled on the latest channel."
                ;;
            stable)
                echo "Auto-upgrade enabled on the stable channel."
                ;;
            *)
                echo "Auto-upgrade enabled on the regular channel."
                ;;
        esac
    else
        rm -f "$LOCK_DIR/auto_upgrade.lck"
        run_auto_upgrade_management disable
        echo "Auto-upgrade disabled."
    fi
fi

if [ "$ACTION_PERFORMED" = true ] && [ -z "$NODE_ROLE" ]; then
    if [ -n "$DEBUG_MODE" ]; then
        if [ -n "$SERVICE" ] || { [ -n "$UPGRADE_CHANNEL" ] && [ -z "$AUTO_UPGRADE_MODE" ]; }; then
            echo "--debug/--no-debug cannot be combined with service or upgrade options without specifying a node role" >&2
            usage
        fi
    fi
    exit 0
fi

if [ -z "$NODE_ROLE" ]; then
    usage
fi

if [ "$DEBUG_OVERRIDDEN" = false ]; then
    if [ "$NODE_ROLE" = "Terminal" ]; then
        write_debug_env "1"
        echo "Terminal role defaults to debug mode."
    else
        write_debug_env "0"
    fi
fi

mkdir -p "$LOCK_DIR"
SERVICE_MANAGEMENT_MODE="$(arthexis_detect_service_mode "$LOCK_DIR")"

if [ -z "$SERVICE" ] && [ -f "$LOCK_DIR/service.lck" ]; then
    SERVICE="$(cat "$LOCK_DIR/service.lck")"
fi


if [ "$REQUIRES_REDIS" = true ]; then
    require_redis "$NODE_ROLE"
fi
SERVICE_ACTIVE=false
if [ "$SKIP_SERVICE_RESTART" != true ] && [ -n "$SERVICE" ] && systemctl list-unit-files | grep -Fq "${SERVICE}.service"; then
    if systemctl is-active --quiet "$SERVICE"; then
        SERVICE_ACTIVE=true
        if ! "$BASE_DIR/stop.sh"; then
            echo "Role change aborted because stop.sh detected active charging sessions. Resolve the sessions or run ./stop.sh --force during a maintenance window." >&2
            exit 1
        fi
    fi
fi

EXISTING_RFID_SERVICE=false
if [ -f "$LOCK_DIR/$ARTHEXIS_RFID_SERVICE_LOCK" ]; then
    EXISTING_RFID_SERVICE=true
fi
EXISTING_CAMERA_SERVICE=false
if [ -f "$LOCK_DIR/$ARTHEXIS_CAMERA_SERVICE_LOCK" ]; then
    EXISTING_CAMERA_SERVICE=true
fi
EXISTING_BOOT_UPGRADE=false
if [ -f "$LOCK_DIR/boot-upgrade.lck" ]; then
    EXISTING_BOOT_UPGRADE=true
fi

if [ "$CELERY_MODE" = "enable" ]; then
    ENABLE_CELERY=true
elif [ "$CELERY_MODE" = "disable" ]; then
    ENABLE_CELERY=false
fi

if [ "$LCD_SCREEN_MODE" = "enable" ]; then
    ENABLE_LCD_SCREEN=true
elif [ "$LCD_SCREEN_MODE" = "disable" ]; then
    ENABLE_LCD_SCREEN=false
fi

for lock_name in celery.lck lcd_screen.lck control.lck role.lck service.lck "$ARTHEXIS_RFID_SERVICE_LOCK" "$ARTHEXIS_CAMERA_SERVICE_LOCK" boot-upgrade.lck; do
    rm -f "$LOCK_DIR/$lock_name"
done
rm -f "$BASE_DIR"/*.role "$BASE_DIR"/.*.role 2>/dev/null || true

if [ "$ENABLE_CELERY" = true ]; then
    touch "$LOCK_DIR/celery.lck"
fi
if [ "$ENABLE_LCD_SCREEN" = true ]; then
    arthexis_enable_lcd_feature_flag "$LOCK_DIR"
fi
if [ "$ENABLE_CONTROL" = true ]; then
    touch "$LOCK_DIR/control.lck"
fi
if [ "$RFID_SERVICE_MODE" = "enable" ]; then
    ENABLE_RFID_SERVICE=true
elif [ "$RFID_SERVICE_MODE" = "disable" ]; then
    ENABLE_RFID_SERVICE=false
else
    ENABLE_RFID_SERVICE="$EXISTING_RFID_SERVICE"
fi
if [ "$ENABLE_RFID_SERVICE" = true ]; then
    touch "$LOCK_DIR/$ARTHEXIS_RFID_SERVICE_LOCK"
fi
if [ "$CAMERA_SERVICE_MODE" = "enable" ]; then
    ENABLE_CAMERA_SERVICE=true
elif [ "$CAMERA_SERVICE_MODE" = "disable" ]; then
    ENABLE_CAMERA_SERVICE=false
else
    ENABLE_CAMERA_SERVICE="$EXISTING_CAMERA_SERVICE"
fi
if [ "$ENABLE_CAMERA_SERVICE" = true ]; then
    touch "$LOCK_DIR/$ARTHEXIS_CAMERA_SERVICE_LOCK"
fi
if [ "$BOOT_UPGRADE_MODE" = "enable" ]; then
    ENABLE_BOOT_UPGRADE=true
elif [ "$BOOT_UPGRADE_MODE" = "disable" ]; then
    ENABLE_BOOT_UPGRADE=false
else
    ENABLE_BOOT_UPGRADE="$EXISTING_BOOT_UPGRADE"
fi
if [ "$ENABLE_BOOT_UPGRADE" = true ]; then
    touch "$LOCK_DIR/boot-upgrade.lck"
fi

echo "$NODE_ROLE" > "$LOCK_DIR/role.lck"
echo "$PORT" > "$LOCK_DIR/backend_port.lck"
if [ -n "$SERVICE" ]; then
    echo "$SERVICE" > "$LOCK_DIR/service.lck"
fi

if [ -n "$SERVICE" ] && arthexis_using_systemd_mode "$LOCK_DIR"; then
    arthexis_install_service_stack "$BASE_DIR" "$LOCK_DIR" "$SERVICE" "$ENABLE_CELERY" "$BASE_DIR/scripts/service-start.sh" "$ARTHEXIS_SERVICE_MODE_SYSTEMD" "$ENABLE_BOOT_UPGRADE"
    if [ "$ENABLE_RFID_SERVICE" = true ]; then
        arthexis_install_rfid_service_unit "$BASE_DIR" "$LOCK_DIR" "$SERVICE"
    else
        arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "rfid-${SERVICE}.service"
    fi
    if [ "$ENABLE_CAMERA_SERVICE" = true ]; then
        arthexis_install_camera_service_unit "$BASE_DIR" "$LOCK_DIR" "$SERVICE"
    else
        arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "camera-${SERVICE}.service"
    fi
    if [ "$ENABLE_BOOT_UPGRADE" = true ]; then
        arthexis_install_boot_upgrade_service_unit "$BASE_DIR" "$LOCK_DIR" "$SERVICE"
    else
        arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${SERVICE}-boot-upgrade.service"
        rm -f "$LOCK_DIR/${SERVICE}-boot-upgrade-backoff-until.lck"
    fi
fi
if [ -n "$SERVICE" ] && ! arthexis_using_systemd_mode "$LOCK_DIR"; then
    arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "${SERVICE}-boot-upgrade.service"
    rm -f "$LOCK_DIR/${SERVICE}-boot-upgrade-backoff-until.lck"
fi


if [ "$AUTO_UPGRADE_MODE" = "enable" ]; then
    channel="${UPGRADE_CHANNEL:-version}"
    echo "$channel" > "$LOCK_DIR/auto_upgrade.lck"
    run_auto_upgrade_management enable
    case "$channel" in
        latest)
            echo "Auto-upgrade enabled on the latest channel."
            ;;
        stable)
            echo "Auto-upgrade enabled on the stable channel."
            ;;
        *)
            echo "Auto-upgrade enabled on the regular channel."
            ;;
    esac
elif [ "$AUTO_UPGRADE_MODE" = "disable" ]; then
    rm -f "$LOCK_DIR/auto_upgrade.lck"
    run_auto_upgrade_management disable
    echo "Auto-upgrade disabled."
fi

if [ "$SERVICE_ACTIVE" = true ]; then
    sudo systemctl start "$SERVICE"
    if [ "$ENABLE_CELERY" = true ]; then
        sudo systemctl start "celery-$SERVICE" || true
        sudo systemctl start "celery-beat-$SERVICE" || true
    fi
    if [ "$ENABLE_LCD_SCREEN" = true ]; then
        sudo systemctl start "lcd-$SERVICE" || true
    fi
    if [ "$ENABLE_RFID_SERVICE" = true ]; then
        arthexis_start_systemd_unit_if_present "rfid-$SERVICE.service"
    fi
    if [ "$ENABLE_CAMERA_SERVICE" = true ]; then
        arthexis_start_systemd_unit_if_present "camera-$SERVICE.service"
    fi
fi

if [ "$REPAIR" = true ]; then
    echo "Repair completed for the $NODE_ROLE role."
fi
