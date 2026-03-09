#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

canonicalize_role() {
    local raw_role="${1:-}"
    local normalized

    normalized="$(printf '%s' "$raw_role" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    case "$normalized" in
        terminal)
            printf 'Terminal\n'
            ;;
        control)
            printf 'Control\n'
            ;;
        satellite)
            printf 'Satellite\n'
            ;;
        watchtower|constellation)
            printf 'Watchtower\n'
            ;;
        *)
            return 1
            ;;
    esac
}

ARTHEXIS_ROLE_PRESET="${ARTHEXIS_ROLE_PRESET:-terminal}"
if ! preset_role="$(canonicalize_role "$ARTHEXIS_ROLE_PRESET")"; then
    echo "Invalid ARTHEXIS_ROLE_PRESET '$ARTHEXIS_ROLE_PRESET'. Expected one of: terminal, control, satellite, watchtower." >&2
    exit 1
fi

node_role_was_explicit=false
if [ -n "${NODE_ROLE:-}" ]; then
    node_role_was_explicit=true
fi

if [ "$node_role_was_explicit" = false ]; then
    export NODE_ROLE="$preset_role"
fi

effective_role="${NODE_ROLE:-$preset_role}"
if canonical_effective_role="$(canonicalize_role "$effective_role" 2>/dev/null)"; then
    effective_role="$canonical_effective_role"
fi

set_toggle_default() {
    local var_name="$1"
    local default_value="$2"

    if [ -z "${!var_name+x}" ]; then
        export "$var_name=$default_value"
    fi
}

set_toggle_default ENABLE_CELERY false
set_toggle_default ENABLE_LCD_SCREEN false
set_toggle_default ENABLE_RFID_SERVICE false
set_toggle_default ENABLE_CAMERA_SERVICE false
set_toggle_default ENABLE_CONTROL false

case "$preset_role" in
    Terminal|Satellite|Watchtower)
        set_toggle_default ENABLE_CELERY true
        ;;
    Control)
        set_toggle_default ENABLE_CELERY true
        set_toggle_default ENABLE_LCD_SCREEN true
        set_toggle_default ENABLE_CONTROL true
        ;;
esac

mkdir -p "$BASE_DIR/.locks"
printf '%s\n' "$effective_role" > "$BASE_DIR/.locks/role.lck"
printf '%s\n' "${PORT:-8888}" > "$BASE_DIR/.locks/backend_port.lck"

cd "$BASE_DIR"
python manage.py migrate

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

exec python manage.py runserver "0.0.0.0:${PORT:-8888}" --noreload
