#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$SCRIPT_DIR"
SCRIPTS_DIR="$BASE_DIR/scripts"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/nginx_maintenance.sh
. "$BASE_DIR/scripts/helpers/nginx_maintenance.sh"
# shellcheck source=scripts/helpers/ports.sh
. "$BASE_DIR/scripts/helpers/ports.sh"
# shellcheck source=scripts/helpers/service_manager.sh
. "$BASE_DIR/scripts/helpers/service_manager.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

SERVICE=""
NODE_ROLE=""
NGINX_MODE="internal"
DISABLE_NGINX=false
PORT=""
ENABLE_CELERY=false
ENABLE_LCD_SCREEN=false
ENABLE_CONTROL=false
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

LOCK_DIR="$BASE_DIR/.locks"

if arthexis_nginx_disabled "$BASE_DIR"; then
    DISABLE_NGINX=true
    NGINX_MODE="none"
fi

usage() {
    echo "Usage: $0 [--service NAME] [--port PORT] [--latest|--stable|--regular] [--check] [--auto-upgrade|--no-auto-upgrade] [--debug|--no-debug] [--satellite|--terminal|--control|--watchtower] [--no-nginx] [--repair [--failover ROLE]]]" >&2
    exit 1
}

write_debug_env() {
    cat > "$BASE_DIR/debug.env" <<EOF
DEBUG=$1
EOF
}

reset_role_features() {
    ENABLE_CELERY=false
    ENABLE_LCD_SCREEN=false
    ENABLE_CONTROL=false
    REQUIRES_REDIS=false
    NGINX_MODE="internal"
    if [ "$DISABLE_NGINX" = true ]; then
        NGINX_MODE="none"
    fi
}

configure_role_from_name() {
    local resolved_role="$1"

    reset_role_features

    case "$resolved_role" in
        Satellite)
            require_nginx "satellite"
            NODE_ROLE="Satellite"
            ENABLE_CELERY=true
            REQUIRES_REDIS=true
            ;;
        Terminal)
            NODE_ROLE="Terminal"
            ENABLE_CELERY=true
            ;;
        Control)
            require_nginx "control"
            NODE_ROLE="Control"
            ENABLE_CELERY=true
            ENABLE_LCD_SCREEN=true
            ENABLE_CONTROL=true
            REQUIRES_REDIS=true
            ;;
        Watchtower)
            require_nginx "watchtower"
            NODE_ROLE="Watchtower"
            ENABLE_CELERY=true
            NGINX_MODE="public"
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

    if [ -f "$LOCK_DIR/nginx_mode.lck" ]; then
        local nginx_mode
        nginx_mode=$(tr -d '\r\n\t ' < "$LOCK_DIR/nginx_mode.lck" 2>/dev/null || true)
        if [ "${nginx_mode,,}" = "public" ]; then
            echo "Watchtower"
            return 0
        fi
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


detect_service_port() {
    local service_name="$1"
    local nginx_mode="$2"
    local port=""
    local fallback="$(arthexis_detect_backend_port "$BASE_DIR")"

    if [ -n "$service_name" ]; then
        local service_file="/etc/systemd/system/${service_name}.service"
        if [ -f "$service_file" ]; then
            port=$(grep -Eo '0\.0\.0\.0:([0-9]+)' "$service_file" | sed -E 's/.*:([0-9]+)/\1/' | tail -n1)
        fi
    fi

    if [ -z "$port" ]; then
        local nginx_conf="/etc/nginx/sites-enabled/arthexis.conf"
        if [ ! -f "$nginx_conf" ]; then
            nginx_conf="/etc/nginx/conf.d/arthexis-${nginx_mode}.conf"
        fi
        if [ -f "$nginx_conf" ]; then
            port=$(grep -E 'proxy_pass http://127\.0\.0\.1:[0-9]+' "$nginx_conf" | head -n1 | sed -E 's/.*127\.0\.0\.1:([0-9]+).*/\1/')
        fi
    fi

    if [ -z "$port" ]; then
        port="$fallback"
    fi

    echo "$port"
}



require_nginx() {
    if [ "$DISABLE_NGINX" = true ] || arthexis_nginx_disabled "$BASE_DIR"; then
        if arthexis_can_manage_nginx; then
            echo "Enabling nginx management for the $1 role."
            DISABLE_NGINX=false
            NGINX_MODE="internal"
            arthexis_enable_nginx "$BASE_DIR"
        else
            echo "Skipping nginx requirement for the $1 role because nginx management is disabled."
            return 0
        fi
    fi

    if ! command -v nginx >/dev/null 2>&1; then
        echo "Nginx is required for the $1 role but is not installed." >&2
        exit 1
    fi
}

require_redis() {
    if ! command -v redis-cli >/dev/null 2>&1; then
        echo "Redis is required for the $1 role but is not installed." >&2
        echo "Install redis-server and re-run this script. For Debian/Ubuntu:" >&2
        echo "  sudo apt-get update && sudo apt-get install redis-server" >&2
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
        --regular)
            set_upgrade_channel "version"
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
        --satellite)
            require_nginx "satellite"
            NODE_ROLE="Satellite"
            ENABLE_CELERY=true
            if [ "$DISABLE_NGINX" != true ]; then
                NGINX_MODE="internal"
            fi
            REQUIRES_REDIS=true
            shift
            ;;
        --terminal)
            NODE_ROLE="Terminal"
            ENABLE_CELERY=true
            if [ "$DISABLE_NGINX" != true ]; then
                NGINX_MODE="internal"
            fi
            shift
            ;;
        --control)
            require_nginx "control"
            NODE_ROLE="Control"
            ENABLE_CELERY=true
            ENABLE_LCD_SCREEN=true
            ENABLE_CONTROL=true
            if [ "$DISABLE_NGINX" != true ]; then
                NGINX_MODE="internal"
            fi
            REQUIRES_REDIS=true
            shift
            ;;
        --watchtower)
            require_nginx "watchtower"
            NODE_ROLE="Watchtower"
            ENABLE_CELERY=true
            if [ "$DISABLE_NGINX" != true ]; then
                NGINX_MODE="public"
            fi
            REQUIRES_REDIS=true
            shift
            ;;
        --no-nginx)
            DISABLE_NGINX=true
            NGINX_MODE="none"
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

if [ -z "$PORT" ]; then
    PORT="$(arthexis_detect_backend_port "$BASE_DIR")"
fi

if [ -n "$FAILOVER_ROLE" ] && [ "$REPAIR" != true ]; then
    echo "--failover can only be used together with --repair" >&2
    usage
fi

if [ "$REPAIR" = true ]; then
    if [ "$CHECK" = true ] || [ -n "$NODE_ROLE" ] || [ -n "$SERVICE" ] || \
       [ -n "$AUTO_UPGRADE_MODE" ] || [ -n "$DEBUG_MODE" ] || [ -n "$UPGRADE_CHANNEL" ]; then
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
       [ -n "$DEBUG_MODE" ] || [ -n "$UPGRADE_CHANNEL" ]; then
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

for lock_name in celery.lck lcd_screen.lck control.lck nginx_mode.lck nginx_disabled.lck role.lck service.lck; do
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

echo "$NGINX_MODE" > "$LOCK_DIR/nginx_mode.lck"
echo "$NODE_ROLE" > "$LOCK_DIR/role.lck"
if [ "$DISABLE_NGINX" = true ]; then
    arthexis_disable_nginx "$BASE_DIR"
else
    arthexis_enable_nginx "$BASE_DIR"
fi
echo "$PORT" > "$LOCK_DIR/backend_port.lck"
if [ -n "$SERVICE" ]; then
    echo "$SERVICE" > "$LOCK_DIR/service.lck"
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

if [ "$DISABLE_NGINX" != true ] && arthexis_can_manage_nginx; then
    https_required=false
    if arthexis_detect_https_enabled "$BASE_DIR" "$NGINX_MODE"; then
        https_required=true
    fi
    arthexis_provision_ssl_options_file "$BASE_DIR" "$https_required"
    arthexis_refresh_nginx_maintenance "$BASE_DIR" "/etc/nginx/sites-enabled/arthexis.conf"
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
fi

if [ "$REPAIR" = true ]; then
    echo "Repair completed for the $NODE_ROLE role."
fi
