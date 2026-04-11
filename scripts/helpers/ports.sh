#!/usr/bin/env bash

# Utility helpers for determining the configured backend port.

# arthexis_detect_live_runserver_port BASE_DIR
#
# Detect the active ``manage.py runserver`` port when a live process is present.
arthexis_detect_live_runserver_port() {
    local base_dir="$1"
    local python_bin=""

    if command -v python3 >/dev/null 2>&1; then
        python_bin=python3
    elif command -v python >/dev/null 2>&1; then
        python_bin=python
    else
        return 1
    fi

    local detected_port=""
    detected_port="$(PYTHONPATH="$base_dir" "$python_bin" -m utils.service_probe detect-runserver-port 2>/dev/null || true)"
    detected_port="$(printf '%s' "$detected_port" | tr -d '\r\n[:space:]')"
    if [[ "$detected_port" =~ ^[0-9]+$ ]] && [ "$detected_port" -ge 1 ] && [ "$detected_port" -le 65535 ]; then
        printf '%s\n' "$detected_port"
        return 0
    fi

    return 1
}

# arthexis_detect_backend_port BASE_DIR [FALLBACK]
#
# Detect the configured backend port for the installation rooted at BASE_DIR.
# If no persisted configuration exists, FALLBACK (default: 8888) is returned.
arthexis_detect_backend_port() {
    local base_dir="$1"
    local fallback="${2:-8888}"
    local lock_file="$base_dir/.locks/backend_port.lck"

    if [ -f "$lock_file" ]; then
        # shellcheck disable=SC2002
        local value
        value="$(cat "$lock_file" | tr -d '\r\n[:space:]')"
        if [[ "$value" =~ ^[0-9]+$ ]]; then
            if [ "$value" -ge 1 ] && [ "$value" -le 65535 ]; then
                printf '%s\n' "$value"
                return 0
            fi
        fi
    fi

    printf '%s\n' "$fallback"
}

# arthexis_service_url BASE_DIR [HOST] [FALLBACK_PORT]
#
# Build the suite URL using the configured backend port.
arthexis_service_url() {
    local base_dir="$1"
    local host="${2:-localhost}"
    local fallback_port="${3:-8888}"
    local port

    port="$(arthexis_detect_backend_port "$base_dir" "$fallback_port")"
    printf 'http://%s:%s\n' "$host" "$port"
}

# arthexis_print_local_admin_login_hint
#
# Print the default admin credential hint with localhost-only scope.
arthexis_print_local_admin_login_hint() {
    echo "Login hint: use admin/admin from the local machine only."
}
