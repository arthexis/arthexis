#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$SCRIPT_DIR"

# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/nginx_maintenance.sh
. "$BASE_DIR/scripts/helpers/nginx_maintenance.sh"
# shellcheck source=scripts/helpers/ports.sh
. "$BASE_DIR/scripts/helpers/ports.sh"

usage() {
    cat <<'USAGE'
Usage: ./nginx-setup.sh [--mode MODE] [--port PORT] [--role ROLE] [--ip6] [--remove] [--no-reload]

Configures the nginx site definition for the current Arthexis installation.
By default the script reads the desired settings from lock files written by
install.sh. Use the flags above to override the persisted values.
USAGE
    exit 1
}

MODE_OVERRIDE=""
PORT_OVERRIDE=""
ROLE_OVERRIDE=""
RELOAD=true
INCLUDE_IPV6=false
REMOVE_SITE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)
            [[ -n "${2:-}" ]] || usage
            MODE_OVERRIDE="$2"
            shift 2
            ;;
        --port)
            [[ -n "${2:-}" ]] || usage
            PORT_OVERRIDE="$2"
            shift 2
            ;;
        --role)
            [[ -n "${2:-}" ]] || usage
            ROLE_OVERRIDE="$2"
            shift 2
            ;;
        --no-reload)
            RELOAD=false
            shift
            ;;
        --ip6)
            INCLUDE_IPV6=true
            shift
            ;;
        --remove)
            REMOVE_SITE=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            usage
            ;;
    esac
done

is_valid_port() {
    local value="$1"
    if [[ "$value" =~ ^[0-9]+$ ]]; then
        if (( value >= 1 && value <= 65535 )); then
            return 0
        fi
    fi
    return 1
}

arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

LOCK_DIR="$BASE_DIR/locks"
mkdir -p "$LOCK_DIR"

if ! arthexis_can_manage_nginx; then
    echo "nginx configuration requires sudo privileges and nginx assets." >&2
    exit 1
fi

if [ "$REMOVE_SITE" = true ]; then
    if [[ -n "$MODE_OVERRIDE" || -n "$ROLE_OVERRIDE" || -n "$PORT_OVERRIDE" || "$INCLUDE_IPV6" == true ]]; then
        echo "--remove cannot be combined with configuration overrides." >&2
        exit 1
    fi

    echo "Removing nginx configuration for Arthexis."
    sudo sh -c 'rm -f /etc/nginx/sites-enabled/arthexis*.conf'
    sudo sh -c 'rm -f /etc/nginx/sites-available/arthexis*.conf' || true
    sudo sh -c 'rm -f /etc/nginx/conf.d/arthexis-*.conf' || true

    if [ "$RELOAD" = true ]; then
        if arthexis_ensure_nginx_in_path && command -v nginx >/dev/null 2>&1; then
            sudo nginx -t
            if ! arthexis_reload_or_start_nginx; then
                echo "Warning: nginx could not be reloaded or started automatically. Ask an administrator to review the service status." >&2
            fi
        else
            echo "nginx not installed; skipping nginx test and reload"
        fi
    else
        echo "Skipping nginx reload per --no-reload flag."
    fi

    exit 0
fi

MODE="internal"
if [ -f "$LOCK_DIR/nginx_mode.lck" ]; then
    MODE="$(tr -d '\r\n[:space:]' < "$LOCK_DIR/nginx_mode.lck" 2>/dev/null || echo internal)"
    [[ -n "$MODE" ]] || MODE="internal"
fi

ROLE="Terminal"
if [ -f "$LOCK_DIR/role.lck" ]; then
    ROLE="$(tr -d '\r\n' < "$LOCK_DIR/role.lck" 2>/dev/null || echo Terminal)"
    [[ -n "$ROLE" ]] || ROLE="Terminal"
fi

PORT="$(arthexis_detect_backend_port "$BASE_DIR")"

if [[ -n "$MODE_OVERRIDE" ]]; then
    MODE="$MODE_OVERRIDE"
fi

if [[ -n "$ROLE_OVERRIDE" ]]; then
    ROLE="$ROLE_OVERRIDE"
fi

if [[ -n "$PORT_OVERRIDE" ]]; then
    if ! is_valid_port "$PORT_OVERRIDE"; then
        echo "Invalid port: $PORT_OVERRIDE" >&2
        exit 1
    fi
    PORT="$PORT_OVERRIDE"
fi

MODE_LOWER="$(printf '%s' "$MODE" | tr '[:upper:]' '[:lower:]')"
case "$MODE_LOWER" in
    internal|public)
        MODE="$MODE_LOWER"
        ;;
    *)
        echo "Unsupported nginx mode: $MODE" >&2
        exit 1
        ;;
esac

echo "$MODE" > "$LOCK_DIR/nginx_mode.lck"
echo "$ROLE" > "$LOCK_DIR/role.lck"
echo "$PORT" > "$LOCK_DIR/backend_port.lck"

echo "Configuring nginx for role '$ROLE' on port $PORT using $MODE mode."

NGINX_CONF="/etc/nginx/sites-enabled/arthexis.conf"

sudo mkdir -p /etc/nginx/sites-enabled
sudo sh -c 'rm -f /etc/nginx/sites-enabled/arthexis*.conf'
sudo sh -c 'rm -f /etc/nginx/sites-enabled/default'
sudo sh -c 'rm -f /etc/nginx/sites-available/default' || true
sudo sh -c 'rm -f /etc/nginx/conf.d/arthexis-*.conf' || true

FALLBACK_SRC_DIR="$BASE_DIR/config/data/nginx/maintenance"
FALLBACK_DEST_DIR="/usr/share/arthexis-fallback"
if [ -d "$FALLBACK_SRC_DIR" ]; then
    sudo mkdir -p "$FALLBACK_DEST_DIR"
    sudo cp -r "$FALLBACK_SRC_DIR"/. "$FALLBACK_DEST_DIR"/
fi

NGINX_RENDERER="$BASE_DIR/scripts/helpers/render_nginx_default.py"
RENDER_CMD=(sudo python3 "$NGINX_RENDERER" --mode "$MODE" --port "$PORT" --dest "$NGINX_CONF")
if [ "$INCLUDE_IPV6" = true ]; then
    RENDER_CMD+=(--ip6)
fi
if ! "${RENDER_CMD[@]}"; then
    echo "Failed to render nginx configuration" >&2
    exit 1
fi

if arthexis_can_manage_nginx; then
    arthexis_refresh_nginx_maintenance "$BASE_DIR" "$NGINX_CONF"
fi

if [ "$RELOAD" = true ]; then
    if arthexis_ensure_nginx_in_path && command -v nginx >/dev/null 2>&1; then
        sudo nginx -t
        if ! arthexis_reload_or_start_nginx; then
            echo "Warning: nginx could not be reloaded or started automatically. Ask an administrator to review the service status." >&2
        fi
    else
        echo "nginx not installed; skipping nginx test and reload"
    fi
else
    echo "Skipping nginx reload per --no-reload flag."
fi
